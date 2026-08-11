"""League scoring engine: SportsData component stats -> points in THIS league's rules.

Point values come straight from the league config (ESPN settings.json, by stat id),
so they're exact. Only the mapping of each ESPN stat id -> SportsData column is
curated here. Used to recompute live projections in league scoring (offense + IDP +
K precisely; D/ST tier scoring is approximated separately).

Validate: python models/scoring.py   (compares vs actual ESPN box totals)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


DST_SLOT = 16          # ESPN lineup-slot id for D/ST; keys pointsOverrides


def load_scoring(year: int = 2024, slot: int | None = None) -> dict[int, float]:
    """ESPN stat id -> points, for a POSITION GROUP.

    ESPN scoring is position-aware and `scoring_format` flattens that away — it
    reports one number per stat and silently loses `pointsOverrides`. That cost us
    real accuracy: sacks are 2.0 for an individual defender but 1.0 for a D/ST, and
    `scoring_format` only ever showed 1.0, so every IDP sack was scored at half
    value. Read the RAW scoringItems instead and apply the slot override.

    slot=None (default)  -> base values: correct for offense and IDP
    slot=DST_SLOT (16)   -> the D/ST column: tackles/PD zeroed, sacks 1.0, PA/YA tiers
    """
    s = json.loads((ROOT / "data" / "raw" / "espn" / str(year) / "settings.json").read_text(encoding="utf-8"))
    raw = s.get("raw_scoring_settings") or {}
    items = raw.get("scoringItems") if isinstance(raw, dict) else None
    if items:
        scoring = {}
        for it in items:
            sid = it.get("statId")
            if sid is None:
                continue
            pts = float(it.get("points", 0) or 0)
            ov = it.get("pointsOverrides") or {}
            if slot is not None and str(slot) in ov:
                pts = float(ov[str(slot)] or 0)
            scoring[int(sid)] = pts
        return scoring
    # legacy seasons without raw settings — fall back to the flattened list
    return {int(it["id"]): float(it.get("points", 0) or 0)
            for it in s.get("scoring_format", []) if it.get("id") is not None}


def _v(col):
    return lambda r: float(r.get(col, 0) or 0)


def _fg_short(r):  # ESPN "FG 0-39" = SportsData 0-19 + 20-29 + 30-39
    return sum(float(r.get(c, 0) or 0) for c in ("FieldGoalsMade0to19", "FieldGoalsMade20to29", "FieldGoalsMade30to39"))


def _fg_missed(r):
    return float(r.get("FieldGoalsAttempted", 0) or 0) - float(r.get("FieldGoalsMade", 0) or 0)


# ESPN stat id -> how to get the stat count from a SportsData row
STAT_MAP = {
    # passing
    3: _v("PassingYards"), 4: _v("PassingTouchdowns"), 20: _v("PassingInterceptions"),
    19: _v("TwoPointConversionPasses"),
    # rushing
    24: _v("RushingYards"), 25: _v("RushingTouchdowns"), 26: _v("TwoPointConversionRuns"),
    # receiving
    42: _v("ReceivingYards"), 43: _v("ReceivingTouchdowns"), 53: _v("Receptions"),
    44: _v("TwoPointConversionReceptions"),
    # misc offense
    72: _v("FumblesLost"), 101: _v("KickReturnTouchdowns"), 102: _v("PuntReturnTouchdowns"),
    # IDP (the DP slot). NOTE: the 2026 export carries solo/asst tackles + passes
    # defensed (ids 108/107/113) stacking on total tackles (109) — but the commish
    # confirmed (2026-07-29) those are rollover defaults, NOT real rules; they're
    # zeroed via league_config.json scoring_overrides. Mapping kept for the day
    # the league actually enables them.
    109: _v("Tackles"), 99: _v("Sacks"), 95: _v("Interceptions"),
    96: _v("FumblesRecovered"), 106: _v("FumblesForced"),
    103: _v("InterceptionReturnTouchdowns"), 104: _v("FumbleReturnTouchdowns"),
    97: _v("BlockedKicks"),
    107: _v("AssistedTackles"), 108: _v("SoloTackles"), 113: _v("PassesDefended"),
    # kicker
    86: _v("ExtraPointsMade"), 80: _fg_short, 77: _v("FieldGoalsMade40to49"),
    198: _v("FieldGoalsMade50Plus"), 85: _fg_missed,
}


def score_row(row: dict, scoring: dict[int, float]) -> float:
    """Project/score one SportsData stat row into league points (offense+IDP+K)."""
    pts = 0.0
    for sid, getter in STAT_MAP.items():
        p = scoring.get(sid)
        if p:
            pts += p * getter(row)
    return round(pts, 2)


def norm_name(n: str) -> str:
    n = re.sub(r"[.'`-]", " ", str(n).lower())
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def fi_key(n: str) -> str:
    """First-initial + last-name key, to match SportsData 'J.Allen' to ESPN 'Josh Allen'."""
    parts = norm_name(n).split()
    if len(parts) >= 2:
        return parts[0][:1] + parts[-1]
    return norm_name(n)


# --------------------------------------------------------------------------- #
# Validation: our scoring of SportsData 2023 actuals vs ESPN box 2023 totals
# --------------------------------------------------------------------------- #
def _validate() -> None:
    scoring = load_scoring(2024)
    stats = pd.read_csv(ROOT / "data" / "raw" / "player_stats.csv")
    stats = stats[stats["Season"] == 2023].copy()
    stats["league_pts"] = stats.apply(lambda r: score_row(r.to_dict(), scoring), axis=1)
    stats["key"] = stats["Name"].map(fi_key)

    espn = pd.read_csv(ROOT / "data" / "processed" / "player_seasons.csv")
    espn = espn[espn["year"] == 2023].copy()
    espn["key"] = espn["name"].map(fi_key)
    espn = espn.rename(columns={"points": "espn_pts"})
    # dedupe both sides on key to avoid many-to-many blowups
    stats = stats.sort_values("league_pts", ascending=False).drop_duplicates("key")
    espn = espn.sort_values("espn_pts", ascending=False).drop_duplicates("key")

    m = stats.merge(espn[["key", "espn_pts", "pos"]], on="key", how="inner")
    skill = m[m["pos"].isin(["QB", "RB", "WR", "TE"])]
    corr = skill["league_pts"].corr(skill["espn_pts"])
    print(f"Validation (2023, offense/skill): matched {len(skill)} players, "
          f"corr(our scoring vs ESPN box) = {corr:.3f}")
    print("(ESPN box counts only rostered weeks, so totals run a bit lower; correlation is the test.)\n")
    print("Top 10 by our league scoring (2023 actuals) — sanity check:")
    top = skill.sort_values("league_pts", ascending=False).head(10)
    for _, r in top.iterrows():
        print(f"  {r['Name']:<22} {r['pos']:<3} ours={r['league_pts']:7.1f}  espn_box={r['espn_pts']:7.1f}")
    idp = m[m["pos"] == "IDP"].sort_values("league_pts", ascending=False).head(5)
    if len(idp):
        print("\nTop IDP by our scoring (validates the DP-slot mapping):")
        for _, r in idp.iterrows():
            print(f"  {r['Name']:<22} IDP ours={r['league_pts']:7.1f}  espn_box={r['espn_pts']:7.1f}")


if __name__ == "__main__":
    _validate()
