"""FantasyPros WEEKLY expert consensus (ECR) — the start/sit second opinion, on trial.

FP gates full weekly PROJECTION tables behind login, but its weekly RANKINGS
pages (the product their own Start/Sit tool runs on) embed the complete ECR
JSON anonymously: rank_ecr, expert-average rank, stddev, opponent. We pull
qb / half-PPR-flex / k / dst, cache one JSON per week, and archive OUR
roster's weekly (ESPN proj, FP rank) pairs to data/processed/
startsit_battle.jsonl so the two sources' pairwise start/sit accuracy can be
graded against actuals as weeks complete.

HONEST GATE (same as the weekly-ML bake-off): FP joins the decision path only
if the accumulated grading beats ESPN. Until then: display-only context.
No IDP — FP publishes no weekly IDP consensus; IDP stays ESPN's.

CLI: python models/fp_weekly.py            -> fetch current week + roster compare
     python models/fp_weekly.py --grade    -> grade archived weeks vs actuals
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "data" / "processed" / "startsit_battle.jsonl"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
PAGES = {
    "QB": "https://www.fantasypros.com/nfl/rankings/qb.php",
    "FLEX": "https://www.fantasypros.com/nfl/rankings/half-point-ppr-flex.php",
    "K": "https://www.fantasypros.com/nfl/rankings/k.php",
    "DST": "https://www.fantasypros.com/nfl/rankings/dst.php",
}
ROS_PAGES = {
    "QB": "https://www.fantasypros.com/nfl/rankings/ros-qb.php",
    "FLEX": "https://www.fantasypros.com/nfl/rankings/ros-half-point-ppr-flex.php",
    "K": "https://www.fantasypros.com/nfl/rankings/ros-k.php",
    "DST": "https://www.fantasypros.com/nfl/rankings/ros-dst.php",
}
_ECR = re.compile(r"var ecrData\s*=\s*(\{.*?\});", re.S)
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm(s: str) -> str:
    s = str(s or "").lower()
    s = _SUFFIX.sub("", s)
    return re.sub(r"[^a-z]", "", s)


def _cache(season: int, week: int) -> Path:
    return ROOT / "data" / "processed" / f"fp_weekly_ecr_{season}_wk{week}.json"


def ros_fetch() -> dict:
    """FP REST-OF-SEASON expert consensus — the third judge for trades. Same
    embedded-ecrData source as the weekly pages, no week param."""
    out: dict = {}
    for page, url in ROS_PAGES.items():
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        m = _ECR.search(r.text)
        if not m:
            raise RuntimeError(f"no ecrData on ROS {page} page")
        players = json.loads(m.group(1)).get("players") or []
        if len(players) < 12:
            raise RuntimeError(f"thin ROS ecrData on {page} ({len(players)})")
        for p in players:
            name = p.get("player_name")
            key = norm(name) if page != "DST" else norm(str(name).split()[-1])
            rec = {"name": name, "page": page, "posRank": p.get("pos_rank"),
                   "avg": float(p.get("rank_ave") or p.get("rank_ecr") or 999)}
            if key not in out or page != "FLEX":
                out[key] = rec
        time.sleep(0.6)
    if len(out) < 200:
        raise RuntimeError(f"FP ROS ECR looks broken ({len(out)} players)")
    return out


def ros_get(season: int = 2026, force: bool = False) -> dict:
    p = ROOT / "data" / "processed" / f"fp_ros_ecr_{season}.json"
    if not force and p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if blob.get("players") and time.time() - blob.get("fetchedAt", 0) < 24 * 3600:
                return blob["players"]
        except Exception:
            pass
    try:
        players = ros_fetch()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"fetchedAt": time.time(), "season": season,
                                 "players": players}), encoding="utf-8")
        return players
    except Exception as e:
        if p.exists():
            try:
                blob = json.loads(p.read_text(encoding="utf-8"))
                if blob.get("players"):
                    print(f"fp ROS: live fetch failed ({e}) — cached copy")
                    return blob["players"]
            except Exception:
                pass
        print(f"fp ROS: unavailable ({e})")
        return {}


def fetch(week: int) -> dict:
    """{norm_name: {name, page, posRank, avg, std, opp}} — lower avg = better."""
    out: dict = {}
    for page, url in PAGES.items():
        r = requests.get(f"{url}?week={week}", headers=UA, timeout=25)
        r.raise_for_status()
        m = _ECR.search(r.text)
        if not m:
            raise RuntimeError(f"no ecrData on {page} page")
        players = json.loads(m.group(1)).get("players") or []
        if len(players) < 12:
            raise RuntimeError(f"thin ecrData on {page} ({len(players)})")
        for p in players:
            name = p.get("player_name")
            key = norm(name) if page != "DST" else norm(str(name).split()[-1])
            rec = {"name": name, "page": page, "posRank": p.get("pos_rank"),
                   "avg": float(p.get("rank_ave") or p.get("rank_ecr") or 999),
                   "std": float(p.get("rank_std") or 0),
                   "opp": p.get("player_opponent")}
            # FLEX page covers RB/WR/TE cross-positionally; keep the flex row
            # unless the player already has a row from a dedicated page
            if key not in out or page != "FLEX":
                out[key] = rec
        time.sleep(0.6)
    if len(out) < 200:
        raise RuntimeError(f"FP weekly ECR looks broken ({len(out)} players)")
    return out


def get(week: int, season: int = 2026, force: bool = False) -> dict:
    p = _cache(season, week)
    if not force and p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if blob.get("players"):
                return blob["players"]
        except Exception:
            pass
    players = fetch(week)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetchedAt": time.time(), "season": season,
                             "week": week, "players": players}), encoding="utf-8")
    return players


# --------------------------------------------------------------------------- #
def roster_compare(season: int = 2026) -> dict:
    """My roster this week: ESPN weekly proj vs FP expert-avg rank, with every
    same-eligibility disagreement flagged. Appends the snapshot to the battle
    archive (one line per player-week) for later grading."""
    try:
        from waivers import startsit
    except ImportError:
        from models.waivers import startsit
    ss = startsit(season)
    if ss.get("error"):
        return ss
    week = ss["week"]
    fp = get(week, season)
    rows = []
    for p in (ss["me"]["current"] + ss["me"]["bench"]):
        if p["pos"] == "DST":   # 'Buccaneers D/ST' -> franchise-nickname key
            key = norm(str(p["name"]).replace("D/ST", "").strip().split()[-1])
        else:
            key = norm(p["name"])
        f = fp.get(key)
        rows.append({"name": p["name"], "pos": p["pos"], "slot": p["slot"],
                     "espnProj": p["proj"],
                     "fpAvg": f["avg"] if f else None,
                     "fpPosRank": f["posRank"] if f else None,
                     "fpStd": f["std"] if f else None,
                     "starting": p["slot"] not in ("BE", "IR")})
    # disagreements. QB/K/DST: direct same-position. Flexables: POOL-level —
    # within RB/WR/TE a slot shuffle can always rearrange assignments, so the
    # started SET is what matters. (Slot-for-slot missed FP's Diggs-over-Pollard
    # lean in wk2 2026 because Pollard occupied the dedicated RB slot.)
    flips = []
    flexable = ("RB", "WR", "TE")
    started = [r for r in rows if r["starting"] and r["fpAvg"] is not None]
    benched = [r for r in rows if not r["starting"] and r["fpAvg"] is not None]

    def _legal_after(out_row, in_row):
        """Dedicated-slot minimums still met if in_row replaces out_row?"""
        counts = {p: sum(1 for r in started if r["pos"] == p) for p in flexable}
        counts[out_row["pos"]] -= 1
        counts[in_row["pos"]] = counts.get(in_row["pos"], 0) + 1
        return counts.get("RB", 0) >= 2 and counts.get("WR", 0) >= 2 and counts.get("TE", 0) >= 1

    for b in benched:
        cands = ([s for s in started if s["pos"] == b["pos"]] if b["pos"] not in flexable
                 else [s for s in started if s["pos"] in flexable and _legal_after(s, b)])
        if not cands:
            continue
        worst = max(cands, key=lambda s: s["fpAvg"])       # FP's weakest started
        fp_flip = b["fpAvg"] < worst["fpAvg"]
        espn_agrees_flip = b["espnProj"] > worst["espnProj"]
        if fp_flip and not espn_agrees_flip:
            flips.append({"fpWouldStart": b["name"], "over": worst["name"],
                          "slot": worst["slot"],
                          "espn": f'{worst["name"]} {worst["espnProj"]} vs {b["name"]} {b["espnProj"]}',
                          "fp": f'{worst["name"]} {worst["fpPosRank"]} (avg {worst["fpAvg"]}) vs '
                                f'{b["name"]} {b["fpPosRank"]} (avg {b["fpAvg"]})'})
    # archive for grading once actuals exist
    try:
        with open(BATTLE, "a", encoding="utf-8") as fh:
            seen = set()
            if BATTLE.exists():
                pass
            fh.write(json.dumps({"week": week, "date": time.strftime("%Y-%m-%d"),
                                 "rows": rows}) + "\n")
    except OSError:
        pass
    return {"week": week, "rows": rows, "flips": flips}


def grade(season: int = 2026) -> None:
    """Grade archived weeks: on same-eligibility pairs where ESPN and FP
    DISAGREED, whose pick scored more actual points? (Actuals from live box.)"""
    try:
        from waivers import _lg
        from inseason import bucket
    except ImportError:
        from models.waivers import _lg
        from models.inseason import bucket
    if not BATTLE.exists():
        print("no battle archive yet")
        return
    snaps = {}
    for line in BATTLE.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        snaps[d["week"]] = d["rows"]        # last snapshot per week wins
    lg = _lg(season)
    cur = int(getattr(lg, "current_week", 1) or 1)
    espn_w = fp_w = ties = 0
    for wk, rows in sorted(snaps.items()):
        if wk >= cur:
            continue                         # week not complete
        actual = {}
        for m in lg.box_scores(wk):
            for side in ("home", "away"):
                for p in (getattr(m, f"{side}_lineup", []) or []):
                    actual[norm(p.name)] = float(getattr(p, "points", 0) or 0)
        flexable = ("RB", "WR", "TE")
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                if a["fpAvg"] is None or b["fpAvg"] is None:
                    continue
                same = a["pos"] == b["pos"] or (a["pos"] in flexable and b["pos"] in flexable)
                if not same:
                    continue
                e_pick = a if a["espnProj"] >= b["espnProj"] else b
                f_pick = a if a["fpAvg"] <= b["fpAvg"] else b
                if e_pick["name"] == f_pick["name"]:
                    continue                 # agreement — no information
                ea, fa = actual.get(norm(e_pick["name"])), actual.get(norm(f_pick["name"]))
                if ea is None or fa is None:
                    continue
                if ea > fa:
                    espn_w += 1
                elif fa > ea:
                    fp_w += 1
                else:
                    ties += 1
    n = espn_w + fp_w + ties
    print(f"ESPN-vs-FP disagreement grading ({n} decided pairs across {len(snaps)} archived weeks):")
    if n:
        print(f"  ESPN right: {espn_w} ({espn_w/n:.0%}) · FP right: {fp_w} ({fp_w/n:.0%}) · ties {ties}")
        print("  gate: FP earns a decision-path seat only with a clear lead at n>=40.")
    else:
        print("  nothing gradable yet — archive accrues each Tuesday, grades after each week")


if __name__ == "__main__":
    if "--grade" in sys.argv:
        grade()
    else:
        cmp_ = roster_compare()
        if cmp_.get("error"):
            raise SystemExit("ERROR: " + str(cmp_["error"]))
        print(f"week {cmp_['week']} · ESPN proj vs FP expert consensus (my roster)")
        for r in sorted(cmp_["rows"], key=lambda x: -(x["espnProj"] or 0)):
            fptxt = f'{r["fpPosRank"]:<5} avg {r["fpAvg"]:<5}' if r["fpAvg"] is not None else "—  (no FP rank)"
            star = "*" if r["starting"] else " "
            print(f' {star} {r["name"]:<24} {r["pos"]:<4} espn {r["espnProj"]:>5}  fp {fptxt}')
        print(f"\nFP-would-flip count: {len(cmp_['flips'])}")
        for f in cmp_["flips"]:
            print(f'  FLIP [{f["slot"]}]: FP starts {f["fpWouldStart"]} over {f["over"]}'
                  f'\n        espn: {f["espn"]} · fp: {f["fp"]}')
