"""Calibrate the value engine on real league history.

Builds league-scored season totals from the ESPN box scores (which are already in
THIS league's scoring), then derives empirical replacement levels per position for
both the historical (12-team/1-FLEX) and 2026 (10-team/2-FLEX) structures.

Outputs:
  data/processed/player_seasons.csv     per player/year: league-scored total, starts
  data/processed/replacement_levels.json replacement points/position per structure

Run: python models/calibrate.py
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "espn"
PROC = ROOT / "data" / "processed"
PROC.mkdir(parents=True, exist_ok=True)
CFG = json.loads((ROOT / "data" / "league_config.json").read_text(encoding="utf-8"))

# ESPN starter-slot keys -> our position buckets
SLOT_TO_BUCKET = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE",
                  "DP": "IDP", "D/ST": "DST", "K": "K"}
ORDER = ["QB", "RB", "WR", "TE", "IDP", "DST", "K"]


def bucket(pos: str) -> str:
    p = str(pos).upper().replace(".", "").replace(" ", "")
    if p in ("RB", "WR", "QB", "TE"):
        return p
    if p in ("K", "PK"):
        return "K"
    if p in ("DST", "D/ST", "DEF", "D"):
        return "DST"
    return "IDP"  # all defensive players -> the DP/IDP slot


def build_player_seasons() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(RAW / "*" / "box_players.csv"))):
        year = int(os.path.basename(os.path.dirname(f)))
        d = pd.read_csv(f)
        d["pos"] = d["position"].map(bucket)
        # one row per player-week -> sum to a league-scored season total
        g = d.groupby("player_id").agg(
            name=("player_name", "first"),
            pos=("pos", lambda s: s.mode().iloc[0] if len(s.mode()) else s.iloc[0]),
            points=("points", "sum"),
            weeks=("week", "nunique"),
            starts=("started", "sum"),
        ).reset_index()
        g["year"] = year
        frames.append(g)
    ps = pd.concat(frames, ignore_index=True)
    ps.to_csv(PROC / "player_seasons.csv", index=False)
    print(f"player_seasons.csv: {len(ps)} player-seasons, {ps.year.nunique()} years "
          f"-> {(PROC / 'player_seasons.csv').relative_to(ROOT)}")
    return ps


def startable_counts(cfg: dict) -> dict:
    """How many of each position start across the whole league (incl. FLEX share)."""
    teams = cfg["teams"]
    counts: dict[str, float] = {}
    for slot, n in cfg["starters"].items():
        b = SLOT_TO_BUCKET.get(slot, slot)
        counts[b] = counts.get(b, 0) + teams * n
    share = cfg.get("flex_share", {})
    for pos in cfg.get("flex_eligible", []):
        counts[pos] = counts.get(pos, 0) + teams * cfg.get("flex_slots", 0) * share.get(pos, 0)
    return counts


def replacement_levels(ps: pd.DataFrame, cfg: dict) -> dict:
    """Replacement = avg (across seasons) of the last-startable player's season points."""
    counts = startable_counts(cfg)
    out = {}
    for pos in ORDER:
        per_year = []
        for _, grp in ps[ps.pos == pos].groupby("year"):
            s = sorted(grp["points"].tolist(), reverse=True)
            if not s:
                continue
            idx = min(len(s) - 1, max(0, round(counts.get(pos, cfg["teams"])) - 1))
            per_year.append(s[idx])
        if per_year:
            out[pos] = round(sum(per_year) / len(per_year), 1)
    return out


def main() -> None:
    ps = build_player_seasons()
    hist = replacement_levels(ps, CFG["historical"])
    new = replacement_levels(ps, CFG["2026"])
    (PROC / "replacement_levels.json").write_text(
        json.dumps({"historical": hist, "2026": new}, indent=2), encoding="utf-8")

    print("\nEmpirical replacement levels — avg season points of the last startable "
          "player (2019-2025 actuals, in your scoring):")
    print(f"{'POS':<5} {'12t/1FLEX (2019-25)':>20} {'10t/2FLEX (2026)':>18}")
    for pos in ORDER:
        print(f"{pos:<5} {hist.get(pos, 0):>20.1f} {new.get(pos, 0):>18.1f}")
    print("\nHigher replacement = shallower need = less draft value at that position.")


if __name__ == "__main__":
    main()
