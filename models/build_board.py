"""Build the DraftIQ board for the live season.

Pipeline: SportsData projections -> score in THIS league's rules (models.scoring)
-> value engine with the 2026 structure (models.value_engine) -> ranked board.
Writes data/processed/board_2026.csv (the cheat sheet) and is reused by api.py.

Run: python models/build_board.py
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

try:  # works both as `python models/build_board.py` and `from models import build_board`
    import dst
    import espn_proj
    import fp_blend
    from scoring import load_scoring, score_row
    from value_engine import League, rank_board, STREAM_DISCOUNT
except ImportError:  # pragma: no cover
    from models import dst, espn_proj, fp_blend
    from models.scoring import load_scoring, score_row
    from models.value_engine import League, rank_board, STREAM_DISCOUNT

OFFENSE_POS = {"QB", "RB", "WR", "TE", "K", "FB"}

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=str(ROOT / ".env"))
KEY = os.getenv("SPORTSDATA_BAKER_KEY")
CFG = json.loads((ROOT / "data" / "league_config.json").read_text(encoding="utf-8"))

SLOT2POS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "DP": "IDP", "D/ST": "DST", "K": "K"}


def league_from_cfg(key: str = "2026") -> League:
    c = CFG[key]
    starters: dict[str, int] = {}
    for slot, n in c["starters"].items():
        b = SLOT2POS.get(slot, slot)
        starters[b] = starters.get(b, 0) + n
    return League(teams=c["teams"], starters=starters, flex_slots=c["flex_slots"],
                  flex_eligible=tuple(c["flex_eligible"]), flex_share=c["flex_share"])


def fetch_projections(season: str = "2026REG") -> list[dict]:
    r = requests.get(
        f"https://api.sportsdata.io/v3/nfl/projections/json/PlayerSeasonProjectionStats/{season}",
        params={"key": KEY}, timeout=30)
    r.raise_for_status()
    return r.json()


def check_idp_feed(rows: list[dict]) -> list[str]:
    """Guard against a half-populated projections feed.

    Seen in the wild (2026-07-28): every established starting LB projected 0.0
    tackles while camp bodies carried the only numbers, and the league-wide max
    projected sacks was 0.7. Rebuilding on that quietly guts the IDP board — in a
    league that STARTS an IDP every week. Cheap to detect, so we
    refuse to write a board like that unless it's forced."""
    def col(name):
        return [float(r.get(name) or 0) for r in rows]
    warnings = []
    max_sacks = max(col("Sacks") or [0])
    max_tackles = max(col("Tackles") or [0])
    n_tackling = sum(1 for v in col("Tackles") if v > 20)
    if max_sacks < 5:
        warnings.append(f"league-wide max projected sacks is {max_sacks:.1f} (expect 12+)")
    if max_tackles < 60:
        warnings.append(f"max projected tackles is {max_tackles:.1f} (a starting LB is 100+)")
    if n_tackling < 150:
        warnings.append(f"only {n_tackling} players project >20 tackles (expect 300+)")
    return warnings


def _players_from_csv(path: Path) -> list[dict]:
    """Reuse the last good board's rows as the player pool (no upstream fetch)."""
    df = pd.read_csv(path)
    keep = ("sd_pts", "fp_pts", "ecr", "ecr_tier", "sos", "fp_adp",
            "fp_best", "fp_worst", "fp_std", "wk25")
    out = []
    for r in df.to_dict("records"):
        if str(r.get("pos")) == "DST":
            continue                      # D/ST is re-derived fresh below
        p = {"playerId": r.get("playerId"), "name": r.get("name"), "team": r.get("team"),
             "position": r.get("pos"), "points": r.get("league_pts"), "adp": r.get("adp")}
        for c in keep:
            if c in r and pd.notna(r[c]):
                p[c] = r[c]
        out.append(p)
    return out


def build(season: str = "2026REG", scoring_year: int = 2026,
          reuse: bool = False, force: bool = False) -> pd.DataFrame:
    # scoring_year 2026: live league settings, position-scoped (see scoring.load_scoring).
    scoring = load_scoring(scoring_year)
    path = ROOT / "data" / "processed" / "board_2026.csv"

    if reuse:
        players = _players_from_csv(path)
        print(f"REUSE: {len(players)} players carried over from {path.name} "
              f"(no projection fetch)")
    else:
        raw = fetch_projections(season)
        problems = check_idp_feed(raw)
        # Broken SportsData IDP is only fatal if we'd actually SHIP it. We now take
        # IDP from ESPN, so probe that first and downgrade the abort to a note.
        espn_idp_ok = False
        if problems:
            try:
                espn_idp_ok = len(espn_proj.get(int(season[:4]))) > 300
            except Exception:
                espn_idp_ok = False
        if problems and not espn_idp_ok and not force:
            raise SystemExit(
                "ABORTING — the SportsData projections feed looks broken:\n  - "
                + "\n  - ".join(problems)
                + "\n\nand ESPN IDP projections are not available as a substitute. Either wait "
                  "for a feed to repopulate, re-run with --reuse to keep the last good "
                  "projections and just refresh D/ST + week-1 odds, or pass --force.")
        if problems:
            print(("NOTE: SportsData IDP is broken (" + problems[0] + ") — "
                   "using ESPN IDP instead." if espn_idp_ok
                   else "WARNING (forced): " + "; ".join(problems)))
        players = [{
            "playerId": r.get("PlayerID"),
            "name": r.get("Name"),
            "team": r.get("Team"),
            "position": r.get("Position") or "",
            "points": score_row(r, scoring),
            "adp": r.get("AverageDraftPositionPPR") or r.get("AverageDraftPosition"),
        } for r in raw]

        # Blend in FantasyPros consensus (skill positions) when the export exists —
        # points becomes 50/50 SportsData + FP-in-league-scoring; sd_pts/fp_pts kept.
        players, n_fp = fp_blend.apply(players, scoring)
        print(f"FantasyPros blend: {n_fp} skill players blended" if n_fp
              else "FantasyPros blend: no export found — SportsData only")
        n_ecr = fp_blend.annotate_ecr(players)
        if n_ecr:
            print(f"FantasyPros ECR annotated: {n_ecr} players (expert rank / tier / SOS)")

    # Durability: weeks played LAST season (league box scores). Measured 2026-08-12
    # on 795 pairs 2019-2025: weeks_t -> weeks_t+1 r=+0.216; players who missed 5+
    # weeks averaged 2.8 fewer weeks + 39 fewer pts the NEXT year. Tiebreaker-grade —
    # display flag only (the ⚕ marker), never a ranking input (market prices it too).
    try:
        from scoring import norm_name as _nn2
        wk_prev = {}
        with open(ROOT / "data" / "processed" / "player_seasons.csv",
                  newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("year") == "2025" and r.get("pos") in ("QB", "RB", "WR", "TE"):
                    try:
                        wk_prev[(_nn2(r["name"]), r["pos"])] = float(r["weeks"])
                    except (ValueError, KeyError):
                        continue
        n_wk = 0
        for p in players:
            k = (_nn2(str(p.get("name", ""))), str(p.get("position", "")).upper())
            if k in wk_prev:
                p["wk25"] = wk_prev[k]
                n_wk += 1
        print(f"durability: 2025 weeks-played attached to {n_wk} skill players")
    except FileNotFoundError:
        pass

    # News overrides (data/manual_overrides.json): feeds lag real news by days —
    # Pearsall was IR'd Aug 1 and SportsData still projected him ~30 pts on Aug 11.
    # out_for_season names are removed entirely so the board can never recommend them.
    try:
        ov = json.loads((ROOT / "data" / "manual_overrides.json").read_text(encoding="utf-8"))
        out_names = {n.lower() for n in ov.get("out_for_season", [])}
        if out_names:
            before = len(players)
            players = [p for p in players if str(p.get("name", "")).lower() not in out_names]
            if before - len(players):
                print(f"news overrides: removed {before - len(players)} out-for-season "
                      f"player(s): {sorted(ov.get('out_for_season', []))}")
        # watch-list matching is SUFFIX-INSENSITIVE: SportsData renames players
        # ('Brian Robinson Jr.' -> 'Brian Robinson'), and an exact match reads a
        # rename as a disappearance (that false alarm shipped once — not again).
        try:
            from scoring import norm_name as _nn
        except ImportError:
            from models.scoring import norm_name as _nn
        have = {_nn(str(p.get("name", ""))) for p in players}
        for nm in ov.get("watch", []):
            if _nn(nm) not in have:
                print(f"⚠ WATCH: '{nm}' is still missing from the feed "
                      f"(see manual_overrides.json note)")
    except FileNotFoundError:
        pass

    # IDP comes from ESPN, not SportsData. SportsData's IDP projections have been
    # broken since 2026-07-28 (starting LBs at 0.0 tackles), and ESPN hands us points
    # already evaluated under this league's position-scoped IDP scoring — which our
    # own re-derivation had been getting wrong anyway (IDP sacks scored at 1.0
    # instead of 2.0, and the solo/assisted/PD stack zeroed by mistake).
    try:
        idp = espn_proj.as_board_rows(season[:4] and int(season[:4]) or 2026)
        if idp:
            players = [p for p in players if str(p.get("position") or "").upper() in OFFENSE_POS]
            players += idp
            print(f"IDP: {len(idp)} defenders from ESPN "
                  f"(top: {idp[0]['name']} {idp[0]['points']:.0f} pts) — SportsData IDP discarded")
    except Exception as e:
        print(f"IDP: ESPN projections unavailable ({e}) — keeping SportsData IDP")

    # Kickers too: SportsData zeroed every FG distance split on 2026-08-10 (the
    # component our scoring ladder consumes), silently collapsing kickers to ~20
    # points. ESPN projects them under the league's real FG ladder.
    try:
        kk = espn_proj.k_board_rows(season[:4] and int(season[:4]) or 2026)
        if kk and max(k["points"] for k in kk) > 120:
            sd_k = [p for p in players if str(p.get("position") or "").upper() == "K"]
            # keep SportsData's ADP where names line up — it's the only K market signal
            adp_by_name = {str(p.get("name")): p.get("adp") for p in sd_k}
            for k in kk:
                k["adp"] = adp_by_name.get(str(k["name"]))
            players = [p for p in players if str(p.get("position") or "").upper() != "K"]
            players += kk
            print(f"K: {len(kk)} kickers from ESPN "
                  f"(top: {kk[0]['name']} {kk[0]['points']:.0f} pts) — SportsData K discarded")
    except Exception as e:
        print(f"K: ESPN projections unavailable ({e}) — keeping SportsData K")

    # Team D/ST lives in a separate SportsData feed, so the player projections above
    # contain none — append them (models/dst.py) or the board has no defenses at all
    # in a league that starts one. Added after the FP blend, which is skill-only.
    try:
        defenses = dst.build(season, scoring_year)
        players += defenses
        print(f"D/ST: {len(defenses)} team defenses added "
              f"(top: {defenses[0]['name']} {defenses[0]['points']:.0f} pts)")
    except Exception as e:                      # never let a D/ST outage block the board
        print(f"D/ST: SKIPPED — {e}")

    board = rank_board(players, league_from_cfg("2026"), overall_pick=1)
    df = pd.DataFrame(board)
    df = df[df["points"] > 0]  # drop non-projected
    df["pos_rank"] = df.groupby("pos")["points"].rank(ascending=False, method="min").astype(int)
    # Big-board priority = VORP discounted for streamable positions (IDP/K/DST).
    df["draft_value"] = df.apply(
        lambda r: round(r["vorp"] * STREAM_DISCOUNT.get(r["pos"], 1.0), 1), axis=1)
    df = df.sort_values("draft_value", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    for c in ("sd_pts", "fp_pts", "ecr", "ecr_tier", "sos", "fp_adp",
              "fp_best", "fp_worst", "fp_std", "wk25"):
        if c not in df.columns:
            df[c] = None
    out = df[["rank", "playerId", "name", "pos", "pos_rank", "team", "points", "vorp",
              "draft_value", "adp", "sd_pts", "fp_pts", "ecr", "ecr_tier", "sos", "fp_adp",
              "fp_best", "fp_worst", "fp_std", "wk25"]] \
        .rename(columns={"points": "league_pts"})
    out.to_csv(path, index=False)
    print(f"board -> {path.relative_to(ROOT)} ({len(out)} players)")
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build the DraftIQ 2026 board.")
    ap.add_argument("--reuse", action="store_true",
                    help="keep the existing board's player projections and only refresh "
                         "D/ST + re-rank (use when the upstream feed is degraded)")
    ap.add_argument("--force", action="store_true",
                    help="build even if the projections feed fails its sanity check")
    a = ap.parse_args()
    df = build(reuse=a.reuse, force=a.force)
    print("\nTop 18 — DraftIQ 2026 board (your scoring, 10-team/2-FLEX, IDP/K/DST stream-discounted):")
    show = df.head(18).copy()
    show["league_pts"] = show["league_pts"].round(0)
    print(show[["rank", "name", "pos", "pos_rank", "team", "league_pts", "vorp", "draft_value", "adp"]].to_string(index=False))
    print("\nWhere the streamable positions now land on the overall board:")
    for pos in ["IDP", "K", "DST"]:
        p = df[df["pos"] == pos].head(1)
        if len(p):
            r = p.iloc[0]
            print(f"  top {pos:<4} {r['name']:<22} overall rank #{int(r['rank']):<4} "
                  f"(raw VORP {r['vorp']:.0f} -> draft_value {r['draft_value']:.0f})")
