"""Backtest: what wins THIS league, and does drafting for value predict it?

Joins each season's draft -> actual league-scored season totals (player_seasons)
-> final standings, then asks:
  1. Which draft metric best predicts a good finish (points-for / standing)?
  2. Do champions build differently (RB-early vs WR-early, when QB goes)?
  3. Does VORP with our calibrated replacement levels correlate with winning?
     (i.e. is "draft for VORP" the right objective in this room?)

Run: python models/backtest.py
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "espn"
PROC = ROOT / "data" / "processed"
REPL = json.loads((PROC / "replacement_levels.json").read_text(encoding="utf-8"))["historical"]


def bucket(pos: str) -> str:
    p = str(pos).upper().replace(".", "").replace(" ", "")
    if p in ("RB", "WR", "QB", "TE"):
        return p
    if p in ("K", "PK"):
        return "K"
    if p in ("DST", "D/ST", "DEF", "D"):
        return "DST"
    if p in ("", "NAN", "NONE"):
        return ""
    return "IDP"


def load_picks() -> pd.DataFrame:
    seasons = pd.read_csv(PROC / "player_seasons.csv")[["player_id", "year", "pos", "points"]]
    frames = []
    for f in sorted(glob.glob(str(RAW / "*" / "draft.csv"))):
        year = int(os.path.basename(os.path.dirname(f)))
        d = pd.read_csv(f)
        d["year"] = year
        frames.append(d)
    picks = pd.concat(frames, ignore_index=True)
    # join actual season points + backfill position from player_seasons (full coverage)
    picks = picks.merge(seasons, on=["player_id", "year"], how="left", suffixes=("", "_act"))
    picks["pos_b"] = picks["pos"].fillna("").map(bucket)
    picks.loc[picks["pos_b"] == "", "pos_b"] = picks["position"].fillna("").map(bucket)
    picks["points"] = picks["points"].fillna(0.0)  # never-played picks produced ~nothing
    picks["repl"] = picks["pos_b"].map(REPL).fillna(0.0)
    picks["vorp"] = (picks["points"] - picks["repl"]).clip(lower=0)
    return picks


def team_season_table(picks: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (year, owner), g in picks.groupby(["year", "owner"]):
        early = g[g["round_num"] <= 5]
        first4 = g[g["round_num"] <= 4]
        qb = g[g["pos_b"] == "QB"]
        rows.append({
            "year": year, "owner": owner,
            "draft_points": g["points"].sum(),
            "draft_vorp": g["vorp"].sum(),
            "early_vorp": early["vorp"].sum(),
            "rb_first4": (first4["pos_b"] == "RB").sum(),
            "wr_first4": (first4["pos_b"] == "WR").sum(),
            "qb_first_round": int(qb["round_num"].min()) if len(qb) else 99,
        })
    ts = pd.DataFrame(rows)
    # attach standings
    st = []
    for f in sorted(glob.glob(str(RAW / "*" / "standings.csv"))):
        year = int(os.path.basename(os.path.dirname(f)))
        s = pd.read_csv(f); s["year"] = year; st.append(s)
    standings = pd.concat(st, ignore_index=True)
    standings["final_standing"] = standings["final_standing"].fillna(standings["reg_season_standing"])
    return ts.merge(standings[["year", "owner", "final_standing", "wins", "points_for"]],
                    on=["year", "owner"], how="left")


def report(ts: pd.DataFrame) -> None:
    print(f"\nTeam-seasons analyzed: {len(ts)}\n")
    print("=== What predicts a strong season? (Spearman corr) ===")
    print("  (vs points_for: + = more is better;  vs final_standing: - = better since 1=1st)")
    for metric in ["draft_points", "draft_vorp", "early_vorp", "rb_first4", "wr_first4"]:
        rp, _ = spearmanr(ts[metric], ts["points_for"])
        rs, _ = spearmanr(ts[metric], ts["final_standing"])
        print(f"  {metric:<14} vs points_for {rp:+.2f}   vs final_standing {rs:+.2f}")
    # QB timing: does taking QB earlier help?
    rq, _ = spearmanr(ts["qb_first_round"].replace(99, ts["qb_first_round"].max()), ts["points_for"])
    print(f"  {'qb_first_round':<14} vs points_for {rq:+.2f}   (- means earlier QB -> more points)")

    print("\n=== Champions vs the field (avg of first-4-round picks) ===")
    champs = ts[ts["final_standing"] <= 3]
    field = ts[ts["final_standing"] >= 7]
    for label, grp in [("Top-3 finishers", champs), ("Bottom-6 finishers", field)]:
        print(f"  {label:<20} RB(1-4)={grp['rb_first4'].mean():.2f}  WR(1-4)={grp['wr_first4'].mean():.2f}  "
              f"first-QB round={grp['qb_first_round'].replace(99, None).mean():.1f}  "
              f"draftVORP={grp['draft_vorp'].mean():.0f}")


if __name__ == "__main__":
    picks = load_picks()
    cov = (picks["pos_b"] != "").mean()
    print(f"Picks: {len(picks)} | position coverage after box-backfill: {cov:.0%} | "
          f"actual-points matched: {(picks['points'] > 0).mean():.0%}")
    ts = team_season_table(picks)
    ts.to_csv(PROC / "team_season_value.csv", index=False)
    report(ts)
