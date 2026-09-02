"""FantasyPros consensus blend for the DraftIQ board.

Reads the user's FantasyPros season ("Draft") projection exports from
data/raw/fantasypros/, scores the COMPONENT stats in THIS league's exact rules
(FP's own FPTS column is standard scoring — ignored), and blends them 50/50 with
the SportsData projection to form the consensus `points` the value engine ranks.

Why: single-source projections fail loudly (2025: one source had Jayden Daniels
at 372; he scored 115). Two sources disagreeing is the warning label — the board
carries sd_pts / fp_pts so big gaps are visible, and the blend softens misses.

Notes
- Files: FantasyPros_Fantasy_Football_Projections_{QB,RB,WR,TE}*.csv. When both a
  stale weekly export and a season export exist, the season one is auto-selected
  (max FPTS scale). QB/RB/WR files carry DUPLICATE column names (pass vs rush vs
  receiving) — parsed positionally per position.
- K/DST/IDP are not blended: FP gives no IDP season table, K lacks distance splits,
  DST isn't in the board — and all three are stream-discounted anyway.

Used by models/build_board.py automatically when the export directory exists.
Run `python models/fp_blend.py` for a standalone self-test.
"""
from __future__ import annotations

import csv
import glob
import re  # noqa: F401  (used by load_ecr's SOS parse)
from pathlib import Path

try:
    from scoring import load_scoring, norm_name, fi_key
except ImportError:  # pragma: no cover
    from models.scoring import load_scoring, norm_name, fi_key

ROOT = Path(__file__).resolve().parents[1]
import os as _os
# guest league can point at its own export set (e.g. the FULL-PPR variant)
FP_DIR = Path(_os.getenv("DRAFTIQ_FP_DIR") or (ROOT / "data" / "raw" / "fantasypros"))

# positional column layout per FP export (headers repeat names, so order matters)
LAYOUT = {
    "QB": ["pass_att", "pass_cmp", "pass_yds", "pass_td", "pass_int",
           "rush_att", "rush_yds", "rush_td", "fl", "fpts"],
    "RB": ["rush_att", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td", "fl", "fpts"],
    "WR": ["rec", "rec_yds", "rec_td", "rush_att", "rush_yds", "rush_td", "fl", "fpts"],
    "TE": ["rec", "rec_yds", "rec_td", "fl", "fpts"],
}

# ESPN scoring ids (see scoring.STAT_MAP) -> our parsed stat keys
SCORE_KEYS = {3: "pass_yds", 4: "pass_td", 20: "pass_int",
              24: "rush_yds", 25: "rush_td",
              42: "rec_yds", 43: "rec_td", 53: "rec",
              72: "fl"}


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _season_file(pos: str) -> Path | None:
    """Pick the season-scale export for a position (max FPTS beats a weekly export)."""
    best, best_scale = None, 0.0
    for f in glob.glob(str(FP_DIR / f"*Projections_{pos}*.csv")):
        try:
            with open(f, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.reader(fh))[1:12]
            scale = max((_num(r[-1]) for r in rows if len(r) > 2), default=0.0)
            if scale > best_scale:
                best, best_scale = Path(f), scale
        except OSError:
            continue
    return best if best_scale > 100 else None   # season totals only; a weekly file never qualifies


def league_points(stats: dict, scoring: dict) -> float:
    """FP component stats -> points under this league's exact ESPN scoring values."""
    return round(sum(scoring.get(sid, 0.0) * stats.get(key, 0.0)
                     for sid, key in SCORE_KEYS.items() if scoring.get(sid)), 1)


def load_fp(scoring: dict) -> dict:
    """{norm_name: {'fp_pts', 'team', 'pos', 'fi': fi_key}} for QB/RB/WR/TE."""
    out: dict[str, dict] = {}
    for pos, cols in LAYOUT.items():
        f = _season_file(pos)
        if not f:
            continue
        with open(f, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        for r in rows[1:]:
            if len(r) < len(cols) + 2 or not str(r[0]).strip():
                continue
            name, team = r[0].strip(), r[1].strip()
            stats = {c: _num(v) for c, v in zip(cols, r[2:2 + len(cols)])}
            if stats.get("fpts", 0) <= 0:
                continue
            key = norm_name(name)
            out[key] = {"fp_pts": league_points(stats, scoring), "team": team,
                        "pos": pos, "fi": fi_key(name)}
    return out


def apply(players: list[dict], scoring: dict, w_fp: float = 0.5) -> tuple[list[dict], int]:
    """Blend FP into SportsData players in place: points -> consensus for matched
    skill players; every player gains sd_pts (+ fp_pts when matched — stored on the
    SD scale so the two columns are directly comparable). Returns (players, matched).

    SD projections run on a much lower scale than FP's (~55%: heavy regression), so
    FP is first RESCALED to the SD scale per position over the matched pool — else a
    "50/50" blend is silently FP-dominated and skill VORP inflates vs the IDP/K pool."""
    fp = load_fp(scoring)
    if not fp:
        return players, 0
    by_fi: dict[str, list] = {}
    for v in fp.values():
        by_fi.setdefault(v["fi"], []).append(v)

    def _match(p, pos):
        hit = fp.get(norm_name(p.get("name", "")))
        if not hit:   # fallback: first-initial+last-name, must agree on team
            cands = [c for c in by_fi.get(fi_key(p.get("name", "")), [])
                     if c["team"] == str(p.get("team", "")).upper() and c["pos"] == pos]
            hit = cands[0] if len(cands) == 1 else None
        return hit if (hit and hit["pos"] == pos) else None

    pairs: dict[str, list] = {}          # pos -> [(player, hit)]
    for p in players:
        p["sd_pts"] = p.get("points")
        pos = str(p.get("position", "")).upper()
        if pos in LAYOUT:
            hit = _match(p, pos)
            if hit:
                pairs.setdefault(pos, []).append((p, hit))

    matched = 0
    for pos, lst in pairs.items():
        core = [(pl, h) for pl, h in lst if (pl["sd_pts"] or 0) > 40 and h["fp_pts"] > 40]
        sd_sum = sum(pl["sd_pts"] for pl, _ in core) or 1.0
        fp_sum = sum(h["fp_pts"] for _, h in core) or 1.0
        scale = max(0.25, min(1.6, sd_sum / fp_sum)) if core else 1.0
        for pl, h in lst:
            pl["fp_pts"] = round(h["fp_pts"] * scale, 1)
            pl["points"] = round((1 - w_fp) * (pl["sd_pts"] or 0) + w_fp * pl["fp_pts"], 2)
            matched += 1
    return players, matched


_IDP_POS = {"LB", "DE", "DT", "CB", "S", "DB", "DL", "EDGE", "IDP"}


def _stars(v) -> int | None:
    """FP coach ratings export as 'N out of 5' -> N (None for '-'/missing)."""
    m = re.match(r"(\d)", str(v or "").strip())
    return int(m.group(1)) if m else None


def load_ecr() -> dict:
    """FP draft boards (FantasyPros_2026_Draft_ALL_Rankings.csv + the separate
    Draft_IDP_Rankings export) -> {norm_name: {'ecr','ecr_tier','sos','fp_adp',
    'fp_up','fp_bust','pos'}}. Header-drift-proof: FP renamed 'SOS SEASON'->'SOS'
    and 'ECR VS. ADP'->'ECR VS ADP' between July and August 2026 exports, and
    ships trailing spaces in 'UPSIDE '/'BUST ' — keys are normalized per row.
    fp_up/fp_bust are FP's coach Upside/Bust ratings (1-5), real values since
    the 8/13 export (earlier files carried placeholder text -> None).
    NOTE: the IDP file's RK is IDP-scoped (1..~205), not overall — fine for
    per-position ordering and coach context; never mix it into overall math."""
    out = {}
    for f in (glob.glob(str(FP_DIR / "*Draft_ALL_Rankings*.csv"))
              + glob.glob(str(FP_DIR / "*Draft_IDP_Rankings*.csv"))):
        with open(f, newline="", encoding="utf-8-sig") as fh:
            for raw in csv.DictReader(fh):
                r = {str(k).strip().rstrip("."): v for k, v in raw.items() if k}
                name = str(r.get("PLAYER NAME", "")).strip()
                rk = str(r.get("RK", "")).strip().strip('"')
                if not name or not rk.isdigit():
                    continue
                sos = re.match(r"(\d)", str(r.get("SOS SEASON") or r.get("SOS") or ""))
                mpos = re.match(r"([A-Z]+)", str(r.get("POS", "")))
                delta = re.match(r"([+-]?\d+)",
                                 str(r.get("ECR VS. ADP") or r.get("ECR VS ADP") or "").strip())
                out[norm_name(name)] = {
                    "ecr": int(rk),
                    "ecr_tier": int(r["TIERS"]) if str(r.get("TIERS", "")).isdigit() else None,
                    "sos": int(sos.group(1)) if sos else None,
                    "pos": mpos.group(1) if mpos else "",
                    "fp_adp": (int(rk) - int(delta.group(1))) if delta else None,
                    "fp_up": _stars(r.get("UPSIDE")),
                    "fp_bust": _stars(r.get("BUST")),
                }
    return out


def load_fp_adp() -> dict:
    """Dedicated FantasyPros ADP export (FantasyPros_2026_Overall_ADP_Rankings.csv,
    from fantasypros.com/nfl/adp — consensus 'AVG' across Yahoo/Sleeper/RTSports)
    -> {norm_name: {'fp_adp', 'pos'}}. PREFERRED market source when present; the
    ALL_Rankings delta derivation is the fallback. Real-file quirks handled: the
    name column is 'Player (Bye)' with team+bye baked into the value
    ('Jahmyr Gibbs   DET (6)', 'Houston Texans DST   (8)') — stripped here."""
    out = {}
    for f in glob.glob(str(FP_DIR / "*Overall_ADP*Rankings*.csv")):
        with open(f, newline="", encoding="utf-8-sig") as fh:
            for raw in csv.DictReader(fh):
                r = {str(k).strip(): v for k, v in raw.items() if k}
                name = str(r.get("Player") or r.get("Player (Bye)")
                           or r.get("PLAYER NAME") or "").strip()
                # strip trailing 'DST', team code, and '(bye)' decorations
                name = re.sub(r"\s+DST\s*(\([^)]*\))?\s*$", "", name)
                name = re.sub(r"\s+[A-Z]{2,3}\s*\(\d+\)\s*$", "", name)
                name = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
                avg = str(r.get("AVG") or r.get("Avg") or r.get("ADP") or "").strip()
                if not name or not avg:
                    continue
                try:
                    adp = float(avg.replace(",", ""))
                except ValueError:
                    continue
                mpos = re.match(r"([A-Z]+)", str(r.get("POS") or r.get("Pos") or ""))
                out[norm_name(name)] = {"fp_adp": round(adp, 1),
                                        "pos": mpos.group(1) if mpos else ""}
    return out


def load_expert_spread() -> dict:
    """Expert-spread signals from FP rankings exports, two supported shapes:
    (A) aggregated BEST/WORST/STD.DEV columns;
    (B) per-expert rank columns (the real 2026 'Overall_Rankings' export ships
        one column per expert, headers like 'Derek Brown... on X 08/13/26') —
        best/worst/std are computed here across the experts.
    BEST = most bullish expert (CEILING read); STD = disagreement (RISK read).
    -> {norm_name: {'best', 'worst', 'std', 'avg', 'pos'}}"""
    out = {}
    for f in glob.glob(str(FP_DIR / "*.csv")):
        try:
            with open(f, newline="", encoding="utf-8-sig") as fh:
                rdr = csv.DictReader(fh)
                fields = [str(h) for h in (rdr.fieldnames or [])]
                heads = {h.strip().upper().replace(" ", "").rstrip(".") for h in fields}
                expert_cols = [h for h in fields if re.search(r"\bon X \d", h)]
                mode_a = ({"BEST", "STD.DEV"} <= heads or {"BEST", "STDDEV"} <= heads)
                if not mode_a and len(expert_cols) < 2:
                    continue
                for r in rdr:
                    rr = {str(k).strip().upper().replace(" ", "").rstrip("."): v
                          for k, v in r.items() if k}
                    name = str(rr.get("PLAYERNAME") or rr.get("PLAYER") or "").strip()
                    if not name:
                        continue
                    if mode_a:
                        try:
                            best = float(str(rr.get("BEST", "")).strip() or 0)
                            std = float(str(rr.get("STD.DEV") or rr.get("STDDEV") or 0) or 0)
                        except ValueError:
                            continue
                        if best <= 0:
                            continue
                        worst, avg = _num(rr.get("WORST")), _num(rr.get("AVG"))
                    else:
                        ranks = []
                        for c in expert_cols:
                            try:
                                v = float(str(r.get(c, "")).strip())
                                ranks.append(v)
                            except (TypeError, ValueError):
                                continue
                        if len(ranks) < 2:
                            continue
                        best, worst = min(ranks), max(ranks)
                        avg = sum(ranks) / len(ranks)
                        std = (sum((x - avg) ** 2 for x in ranks) / (len(ranks) - 1)) ** 0.5
                    mpos = re.match(r"([A-Z]+)", str(rr.get("POSITION") or rr.get("POS") or ""))
                    out[norm_name(name)] = {"best": best, "worst": worst or None,
                                            "std": round(std, 1) if std else None,
                                            "avg": round(avg, 1) if avg else None,
                                            "pos": mpos.group(1) if mpos else ""}
        except OSError:
            continue
    return out


def annotate_ecr(players: list[dict]) -> int:
    """Attach ecr / ecr_tier / sos / fp_adp (+ fp_best/fp_worst/fp_std when a
    spread export exists) to matched players (no effect on points/VORP).
    Position-guarded: an IDP sharing a name with a skill player (e.g. the
    defensive Justin Jefferson) must not inherit the WR's expert rank.
    fp_adp preference: dedicated ADP export > ALL_Rankings delta derivation."""
    ecr = load_ecr()
    adp = load_fp_adp()
    spread = load_expert_spread()
    n = 0
    for p in players:
        key = norm_name(p.get("name", ""))
        hit = ecr.get(key)
        if not hit:
            continue
        ppos = str(p.get("position", "")).upper()
        # exact position match for skill; IDP-bucket match for defenders (FP says
        # 'LB55' where ESPN says DE for edge players — both are the DP slot here)
        if not (hit["pos"] == ppos or (hit["pos"] in _IDP_POS and ppos in _IDP_POS)):
            continue
        p["ecr"], p["ecr_tier"], p["sos"] = hit["ecr"], hit["ecr_tier"], hit["sos"]
        if hit.get("fp_up") is not None:
            p["fp_up"] = hit["fp_up"]
        if hit.get("fp_bust") is not None:
            p["fp_bust"] = hit["fp_bust"]
        a = adp.get(key)
        p["fp_adp"] = (a["fp_adp"] if a and a["pos"] == hit["pos"] else hit.get("fp_adp"))
        s = spread.get(key)
        if s and (not s["pos"] or s["pos"] == hit["pos"]
                  or (s["pos"] in _IDP_POS and ppos in _IDP_POS)):
            p["fp_best"], p["fp_worst"], p["fp_std"] = s["best"], s["worst"], s["std"]
        n += 1
    return n


# --------------------------------------------------------------------------- #
# Self-test — `python models/fp_blend.py`
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    sc = load_scoring(2024)
    fp = load_fp(sc)
    print(f"FP players loaded (season files, league-scored): {len(fp)}")
    for pos in LAYOUT:
        f = _season_file(pos)
        n = sum(1 for v in fp.values() if v["pos"] == pos)
        print(f"  {pos}: {n:>3} players  <- {f.name if f else 'MISSING'}")
    top = sorted(fp.items(), key=lambda kv: -kv[1]["fp_pts"])[:10]
    print("\nTop 10 by FP consensus IN LEAGUE SCORING (half-PPR etc.):")
    for k, v in top:
        print(f"  {k:<24} {v['pos']:<3} {v['team']:<4} {v['fp_pts']:>6.1f}")
