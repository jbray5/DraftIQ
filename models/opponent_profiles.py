"""Per-manager draft profiles for the AI coach.

Turns 7 seasons of drafts + results into a tendency sheet per owner: when they take
each position vs the room, their early-round lean, and their track record. Output is
data/processed/manager_profiles.json, which the coach loads to be opponent-aware.

Run: python models/opponent_profiles.py
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import pandas as pd

from backtest import load_picks  # reuse the draft<->actuals join

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "espn"
PROC = ROOT / "data" / "processed"
CFG = json.loads((ROOT / "data" / "league_config.json").read_text(encoding="utf-8"))
DEPARTING = set(CFG["2026"].get("departing_owners", []))

# TENDENCIES use recent drafts only. History now reaches back to 2012, but a
# manager's 2014 self is not a prior on their 2026 self (user's call, and the data
# agreed: blending eras washed out Munford's RB-heavy read). Titles/finishes stay
# lifetime — a championship doesn't expire.
TENDENCY_SINCE = 2019


def standings_all() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(RAW / "*" / "standings.csv"))):
        year = int(os.path.basename(os.path.dirname(f)))
        s = pd.read_csv(f); s["year"] = year; frames.append(s)
    s = pd.concat(frames, ignore_index=True)
    s["final_standing"] = s["final_standing"].fillna(s["reg_season_standing"])
    return s


def primary_owner(o) -> str:
    """Profiles are keyed by PERSON, not team — teams change managers over the years.
    Collapse multi-account owner strings ('a; b') to the primary handle so one person
    isn't split across profiles."""
    return str(o).split(";")[0].strip()


def main() -> None:
    picks = load_picks()
    picks = picks[picks["owner"].notna()].copy()
    picks["owner"] = picks["owner"].map(primary_owner)
    standings = standings_all()
    standings["owner"] = standings["owner"].map(primary_owner)
    latest_year = int(standings["year"].max())
    active_owners = set(standings[standings["year"] == latest_year]["owner"].dropna())

    # league baselines for "vs the room" comparisons — same recency window as
    # the per-manager tendencies, or the comparison is apples-to-oranges
    recent_picks = picks[picks["year"] >= TENDENCY_SINCE]
    base_qb = recent_picks[recent_picks["pos_b"] == "QB"].groupby(["year", "owner"])["round_num"].min().mean()

    profiles = {}
    for owner, g_all in picks.groupby("owner"):
        if pd.isna(owner):
            continue
        seasons = sorted(g_all["year"].unique().tolist())          # lifetime, for the record
        g = g_all[g_all["year"] >= TENDENCY_SINCE]                 # tendencies: recent only
        srows = standings[standings["owner"] == owner]
        titles = int((srows["final_standing"] == 1).sum())
        avg_finish = round(srows["final_standing"].mean(), 1) if len(srows) else None
        recent = srows.sort_values("year")
        recent_team = recent.iloc[-1]["team_name"] if len(recent) else None

        f4 = g[g["round_num"] <= 4]
        qb = g[g["pos_b"] == "QB"]
        qb_first = round(qb.groupby("year")["round_num"].min().mean(), 1) if len(qb) else None
        rb_e = round((f4["pos_b"] == "RB").mean() * 4, 2)   # ~RBs in first 4 picks
        wr_e = round((f4["pos_b"] == "WR").mean() * 4, 2)

        # Sample size matters: a team can exist for years but its CURRENT manager
        # may have only drafted once or twice. Confidence is about the TENDENCY
        # sample, so count recent drafts, not lifetime seasons.
        drafts = int(g["year"].nunique())
        confidence = "high" if drafts >= 4 else "medium" if drafts >= 2 else "low"
        bits = []
        if confidence == "low":
            bits.append(f"new/low-data manager — only {drafts} draft of history; tendencies unreliable")
        else:
            if qb_first is not None and qb_first <= base_qb - 1.0:
                bits.append(f"takes QB early (~rd {qb_first} vs room {base_qb:.1f})")
            elif qb_first is not None and qb_first >= base_qb + 1.0:
                bits.append(f"waits on QB (~rd {qb_first} vs room {base_qb:.1f})")
            if rb_e >= 2.2:
                bits.append("RB-heavy early")
            elif wr_e >= 2.2:
                bits.append("WR-heavy early")
            else:
                bits.append("balanced RB/WR early")
            if confidence == "medium":
                bits.append(f"(medium confidence, {drafts} drafts)")
        record = f"{titles} title(s), avg finish {avg_finish}" if avg_finish else "limited history"
        is_departing = any(dep in str(owner) for dep in DEPARTING)

        profiles[str(owner)] = {
            "recent_team": recent_team,
            "active_2026": (owner in active_owners) and not is_departing,
            "seasons": seasons,
            "drafts": drafts,
            "confidence": confidence,
            "titles": titles,
            "avg_finish": avg_finish,
            # only expose a QB-timing read when there's enough data to mean something
            "qb_first_round_avg": qb_first if drafts >= 2 else None,
            "rb_in_first4": rb_e,
            "wr_in_first4": wr_e,
            "summary": f"{recent_team or owner}: " + "; ".join(bits) + f". {record}.",
        }

    out = PROC / "manager_profiles.json"
    out.write_text(json.dumps(profiles, indent=2, default=str), encoding="utf-8")
    active = [p for p in profiles.values() if p["active_2026"]]
    print(f"manager_profiles.json: {len(profiles)} managers ({len(active)} active for 2026) "
          f"-> {out.relative_to(ROOT)}\n")
    print("Active 2026 managers — tendencies:")
    for p in sorted(active, key=lambda x: (x["avg_finish"] or 99)):
        print(f"  - {p['summary']}")


if __name__ == "__main__":
    main()
