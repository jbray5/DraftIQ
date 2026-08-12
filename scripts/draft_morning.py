"""DRAFT MORNING — the whole data refresh as one command.

    python scripts/draft_morning.py

Runs, in order:
  1. ESPN league pull (2026)      — settings/rosters; catches any commish scoring change
  2. Scoring-config diff          — warns loudly if the IDP tackle config moved
  3. Force-refresh ESPN caches    — IDP + K projections, week-1 odds, headshots
  4. Full board rebuild           — fresh SportsData skill + everything above
  5. Board diff                   — biggest movers vs the previous board (news check)

Exit code 0 = every stage succeeded. Run F9 in the app afterward for the final word.
FantasyPros exports are the one manual step — download fresh ones into
data/raw/fantasypros/ BEFORE running this (all while logged in):
  1. Rankings -> Draft -> Export        (FantasyPros_2026_Draft_ALL_Rankings.csv)
  2. ADP page -> Export                 (FantasyPros_2026_Overall_ADP_Rankings.csv)
  3. Rankings export WITH the BEST/WORST/STD.DEV columns (the consensus-rankings
     download) — powers the UPS column's ceiling/bust flags. Any filename works;
     it's auto-detected by header.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
sys.path.insert(0, str(ROOT / "models"))
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def stage(n, title):
    print(f"\n{'=' * 64}\n[{n}/5] {title}\n{'=' * 64}")


def idp_config(path):
    s = json.loads(Path(path).read_text(encoding="utf-8"))
    items = (s.get("raw_scoring_settings") or {}).get("scoringItems", [])
    return {it["statId"]: (it.get("points"), str(it.get("pointsOverrides")))
            for it in items if it.get("statId") in (99, 107, 108, 109, 113)}


def main() -> int:
    t0 = time.time()
    settings = ROOT / "data/raw/espn/2026/settings.json"
    board = ROOT / "data/processed/board_2026.csv"
    pre_cfg = idp_config(settings) if settings.exists() else {}
    pre_board = {}
    if board.exists():
        shutil.copy(board, board.with_suffix(".prev_refresh.csv"))
        pre_board = {r["name"] + "|" + r["pos"]: r
                     for r in csv.DictReader(open(board, encoding="utf-8"))}

    # FantasyPros staleness warning up front
    fp = ROOT / "data/raw/fantasypros/FantasyPros_2026_Draft_ALL_Rankings.csv"
    if fp.exists():
        age_d = (time.time() - fp.stat().st_mtime) / 86400
        if age_d > 3:
            print(f"⚠ FantasyPros export is {age_d:.0f} days old — download fresh ones "
                  f"into data/raw/fantasypros/ for current ECR/ADP")

    stage(1, "ESPN league pull (2026)")
    r = subprocess.run([PY, str(ROOT / "scripts/pull_espn.py"), "--years", "2026"],
                       cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip()[-400:] or r.stderr.strip()[-400:])
    if r.returncode != 0:
        FAILS.append("ESPN league pull")

    stage(2, "Scoring-config diff (did the commish act?)")
    post_cfg = idp_config(settings)
    names = {99: "Sacks", 107: "AsstTackles", 108: "SoloTackles",
             109: "TotalTackles", 113: "PassesDef"}
    changed = [sid for sid in names if pre_cfg.get(sid) != post_cfg.get(sid)]
    if changed and pre_cfg:
        print("🚨 SCORING CHANGED since the last pull:")
        for sid in changed:
            print(f"   {names[sid]}: {pre_cfg.get(sid)} -> {post_cfg.get(sid)}")
        print("   The rebuild below re-prices everything under the NEW rules.")
    else:
        tk = next((v[0] for sid, v in post_cfg.items() if sid == 109), "?")
        has_solo = any(sid in (107, 108) for sid in post_cfg)
        print(f"   unchanged — tackles {tk} flat (commish ruling 2026-08-11)"
              + ("  ⚠ solo/asst items present again — inspect!" if has_solo else ""))

    stage(3, "Force-refresh ESPN caches (IDP / K / week-1 odds / headshots)")
    try:
        import espn_proj, week1_odds, headshots
        idp = espn_proj.get(2026, force=True)
        print(f"   IDP: {len(idp)} (top {idp[0]['name']} {idp[0]['points']:.0f})")
        kk = espn_proj.get_k(2026, force=True)
        print(f"   K:   {len(kk)} (top {kk[0]['name']} {kk[0]['points']:.0f})")
        w1 = week1_odds.get(2026, 1, force=True)
        lined = [g for g in w1["games"] if g.get("overUnder") is not None]
        print(f"   W1:  {len(lined)}/{len(w1['games'])} games lined")
        hs = headshots.get_map(force=True)
        print(f"   headshots: {len(hs)}")
        if len(idp) < 300 or len(kk) < 25 or len(lined) < 12:
            FAILS.append("ESPN caches (thin results)")
    except Exception as e:
        print(f"   FAILED: {e}")
        FAILS.append("ESPN caches")

    stage(4, "Full board rebuild")
    r = subprocess.run([PY, str(ROOT / "models/build_board.py")],
                       cwd=ROOT / "models", capture_output=True, text=True)
    tail = (r.stdout or "") + (r.stderr or "")
    print("\n".join(tail.strip().splitlines()[:8]))
    if r.returncode != 0 or not board.exists():
        FAILS.append("board rebuild")

    stage(5, "Board diff vs previous (news check)")
    if pre_board:
        # key by playerId FIRST — feeds rename players ('Brian Robinson Jr.' ->
        # 'Brian Robinson'), and a name-keyed diff misreports a rename as a
        # disappearance + appearance. Fall back to name|pos only when id is blank.
        def keyed(rows_by_namepos):
            out = {}
            for r in rows_by_namepos.values():
                pid = str(r.get("playerId") or "").strip()
                out[pid if pid else r["name"] + "|" + r["pos"]] = r
            return out
        post_np = {r["name"] + "|" + r["pos"]: r
                   for r in csv.DictReader(open(board, encoding="utf-8"))}
        pre, post = keyed(pre_board), keyed(post_np)
        movers, renames = [], []
        for k in set(pre) & set(post):
            if post[k]["pos"] not in ("QB", "RB", "WR", "TE"):
                continue
            if pre[k]["name"] != post[k]["name"]:
                renames.append(f"{pre[k]['name']} -> {post[k]['name']}")
            try:
                d = float(post[k]["league_pts"]) - float(pre[k]["league_pts"])
            except ValueError:
                continue
            if abs(d) >= 5:
                movers.append((d, post[k]["name"], post[k]["pos"]))
        movers.sort(key=lambda x: -abs(x[0]))
        print(f"   {len(post)} rows ({len(set(post) - set(pre))} new ids, "
              f"{len(set(pre) - set(post))} gone)")
        if renames:
            print(f"   renamed (same player, cosmetic): {renames[:4]}")
        if movers:
            print("   biggest movers (check for injury/depth-chart news!):")
            for d, nm, pos in movers[:8]:
                print(f"     {nm:<24}{pos:<4}{d:+7.1f}")
        gone = [pre[k]["name"] for k in set(pre) - set(post)
                if pre[k]["pos"] in ("QB", "RB", "WR", "TE")
                and float(pre[k]["league_pts"] or 0) > 40]
        if gone:
            print(f"   ⚠ players truly GONE from the feed (id-matched): {gone[:6]} — verify manually")
    else:
        print("   (no previous board to diff)")

    print(f"\n{'=' * 64}")
    if FAILS:
        print(f"✗ REFRESH FINISHED WITH FAILURES: {', '.join(FAILS)}")
        print("  Fix or fall back: the committed draft-day snapshot still works.")
    else:
        print(f"✓ ALL 5 STAGES CLEAN in {time.time() - t0:.0f}s — now press F9 in the app.")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
