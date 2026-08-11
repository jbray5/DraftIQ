"""DraftIQ in-season engine — start/sit, waivers, trades, in YOUR league scoring.

Pure, league-config-driven functions plus a historical validation that quantifies
the start/sit points you left on your bench across 2019-2025 (a real edge to
capture). Live weekly projections (SportsData) and free-agent lists (ESPN) wire in
once the 2026 season starts; trade/ROS value already runs on 2026 projections.

Run: python models/inseason.py
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "espn"
CFG = json.loads((ROOT / "data" / "league_config.json").read_text(encoding="utf-8"))

SLOT_ELIG = {"QB": ["QB"], "RB": ["RB"], "WR": ["WR"], "TE": ["TE"],
             "DP": ["IDP"], "D/ST": ["DST"], "K": ["K"]}


def bucket(pos: str) -> str:
    p = str(pos).upper().replace(".", "").replace(" ", "").replace("/", "")
    if p in ("RB", "WR", "QB", "TE"):
        return p
    if p in ("K", "PK"):
        return "K"
    if p in ("DST", "DEF", "D"):
        return "DST"
    return "IDP"


def slot_spec(cfg: dict) -> list[tuple[str, list[str]]]:
    """Ordered starting slots as (label, eligible-positions), incl. FLEX(es)."""
    spec = []
    for slot, n in cfg["starters"].items():
        for _ in range(n):
            spec.append((slot.replace("D/ST", "DST"), SLOT_ELIG.get(slot, [bucket(slot)])))
    for _ in range(cfg.get("flex_slots", 0)):
        spec.append(("FLEX", list(cfg.get("flex_eligible", ["RB", "WR", "TE"]))))
    return spec


def optimal_lineup(players: list[dict], spec: list[tuple[str, list[str]]]) -> dict:
    """Best legal lineup by projected (or actual) points. Greedy by most-constrained
    slot first, FLEX last with the best leftover — optimal for exclusive slots + FLEX."""
    avail = sorted(players, key=lambda p: -(p.get("proj") or 0))
    used, starters = set(), []
    for slot, elig in sorted(spec, key=lambda s: len(s[1])):  # fixed slots before FLEX
        for i, p in enumerate(avail):
            if i not in used and bucket(p["pos"]) in elig:
                starters.append((slot, p)); used.add(i); break
    total = sum((p.get("proj") or 0) for _, p in starters)
    bench = [p for i, p in enumerate(avail) if i not in used]
    return {"starters": starters, "bench": bench, "total": round(total, 1)}


def waiver_targets(my_roster: list[dict], free_agents: list[dict],
                   replacement: dict, spec: list, top: int = 12) -> list[dict]:
    """Rank free agents by value over replacement, flagging ones that crack your lineup."""
    base = optimal_lineup(my_roster, spec)["total"]
    out = []
    for fa in free_agents:
        pos = bucket(fa["pos"])
        vorp = (fa.get("proj") or 0) - replacement.get(pos, 0)
        starts = optimal_lineup(my_roster + [fa], spec)["total"] - base  # lineup upgrade if added
        out.append({**fa, "vorp": round(vorp, 1), "lineup_gain": round(starts, 1),
                    "starts_now": starts > 0.1})
    return sorted(out, key=lambda x: (-x["lineup_gain"], -x["vorp"]))[:top]


def trade_eval(my_roster: list[dict], give: list[str], get: list[dict], spec: list) -> dict:
    """ROS impact of a proposed trade on YOUR side (lineup points + raw value)."""
    before = optimal_lineup(my_roster, spec)["total"]
    after_roster = [p for p in my_roster if p["name"] not in set(give)] + get
    after = optimal_lineup(after_roster, spec)["total"]
    give_val = sum((p.get("proj") or 0) for p in my_roster if p["name"] in set(give))
    get_val = sum((p.get("proj") or 0) for p in get)
    delta = round(after - before, 1)
    verdict = "ACCEPT" if delta >= 5 else "DECLINE" if delta <= -5 else "FAIR/COUNTER"
    return {"lineup_delta": delta, "give_value": round(give_val, 1),
            "get_value": round(get_val, 1), "verdict": verdict}


# --------------------------------------------------------------------------- #
# Historical validation: start/sit points left on the bench (2019-2025)
# --------------------------------------------------------------------------- #
def _validate_start_sit() -> None:
    spec = slot_spec(CFG["historical"])  # 12-team / 1-FLEX era
    rows = []
    for f in sorted(glob.glob(str(RAW / "*" / "box_players.csv"))):
        year = int(os.path.basename(os.path.dirname(f)))
        d = pd.read_csv(f)
        d = d[d["is_playoff"] == False] if "is_playoff" in d else d  # regular season
        for (wk, tid, tname), g in d.groupby(["week", "team_id", "team_name"]):
            roster = [{"name": r["player_name"], "pos": r["position"], "proj": r["points"]}
                      for _, r in g.iterrows() if pd.notna(r["points"])]
            if len(roster) < len(spec):
                continue
            actual = g[g["started"] == True]["points"].sum()
            opt = optimal_lineup(roster, spec)["total"]
            rows.append({"year": year, "team_name": tname, "owner_team": tid,
                         "actual": actual, "optimal": opt, "left_on_bench": opt - actual})
    df = pd.DataFrame(rows)
    if df.empty:
        print("No box data — run scripts/pull_espn.py --box first.")
        return
    league_avg = df["left_on_bench"].mean()
    print(f"Start/sit validation — {len(df)} team-weeks (2019-2025 regular season)")
    print(f"League-wide: avg {league_avg:.1f} optimal pts left on the bench per team-week.\n")
    me = df[df["team_name"].str.contains("SCLSU", na=False)]
    if len(me):
        print(f"YOU (SCLSU Mud Dogs): avg {me['left_on_bench'].mean():.1f} left/week "
              f"over {len(me)} weeks — that's ~{me['left_on_bench'].mean()*14:.0f} pts/season "
              f"a perfect-hindsight optimizer would have added.")
        print("(Live, the optimizer uses projections — it won't hit the hindsight ceiling, "
              "but consistently capturing even a third of this is real standings movement.)")
    print("\nBiggest single-week misses (most left on bench):")
    for _, r in df.sort_values("left_on_bench", ascending=False).head(5).iterrows():
        print(f"  {r['year']} {r['team_name'][:22]:<22} left {r['left_on_bench']:5.1f} "
              f"(started {r['actual']:.0f} vs optimal {r['optimal']:.0f})")


if __name__ == "__main__":
    _validate_start_sit()
