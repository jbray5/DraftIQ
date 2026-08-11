"""Team D/ST projections for the DraftIQ board.

Why this exists: SportsData's PlayerSeasonProjectionStats feed (what build_board
uses) contains only individual players, so the board had NO D/ST rows at all — in
a league that starts one. The UI papered over the hole by inventing 32 defenses
client-side with a fabricated linear ramp (proj 120, 118.8, 117.6 ...) and a flat
vorp of -2, which meant the pick engine and the coach were reasoning about numbers
nobody computed.

The approach, and its honest limits:

  ORDER comes from SportsData's D/ST projection feed (FantasyDefenseProjections-
  BySeason, which IS in our plan), scored in this league's exact rules.

  SCALE comes from this league's own history. SportsData's team-level magnitudes
  are not trustworthy — their 2025 team feeds report season sack totals up to 105
  (the NFL record is 72) and disagree with their own weekly endpoint — so we use
  their numbers only to rank the 32 defenses, then map that rank onto the real
  points-per-week curve measured from 7 seasons of ESPN box scores.

  SHRINKAGE is applied because D/ST is barely predictable, and the size of that
  effect is easy to get wrong. Season-total D/ST scoring looks reassuringly
  persistent year over year (+0.47) — but that is an artifact: total points
  correlate +0.87 with the number of weeks a defense was rostered, so the "signal"
  is mostly that good defenses stay rostered while bad ones get dropped. Measured
  on the scoring RATE (points per week), persistence collapses to +0.08 (all) /
  +0.14 (pooled, defenses rostered 8+ weeks in both years), swinging between -0.69
  and +0.72 across individual year pairs. Only 0.83 of the top 3 defenses repeat a
  top-3 finish in the following season, and in three of six transitions none did.
  ESPN's own weekly D/ST projections correlate just +0.27 with what happens (vs
  +0.52 for skill players).

Net: the raw DST1-vs-DST11 gap of ~77 pts/season is NOT a gap you can draft into.
After shrinkage the top defense is worth roughly 10 pts over a full season — about
0.6 pts/week, a rounding error next to a round-1 RB's ~67 VORP. The model exists
so the board says that with real numbers instead of inventing precision.

Run `python models/dst.py` to rebuild and print the projections + the curve.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=str(ROOT / ".env"))
KEY = os.getenv("SPORTSDATA_BAKER_KEY")

WEEKS = 17          # fantasy regular season + the weeks a defense is rostered
MIN_WEEKS = 4       # ignore defenses barely rostered when measuring the curve
RHO_MIN_WEEKS = 8   # persistence is measured on well-sampled defenses only: a
                    # 4-week points-per-week average is so noisy it attenuates the
                    # correlation toward zero (measurement error, not real signal)

# NFL-true team-defense distributions, used to rescale SportsData's projection
# magnitudes before the tiered points/yards-allowed scoring is applied. Their
# feed is internally consistent but miscalibrated (it projects a 16.6 pts/game
# league average against a real ~22), which would otherwise push every defense
# into the same scoring tier and flatten the ordering.
NFL_REAL = {"pa_pg": (22.0, 3.5), "ya_pg": (330.0, 35.0)}

# ESPN stat ids for the D/ST tiers (verified against data/raw/espn/2026/settings.json)
PA_TIERS = [(0, 0, 89), (1, 6, 90), (7, 13, 91), (14, 17, 92), (18, 27, None),
            (28, 34, 123), (35, 45, 124), (46, 999, 125)]
YA_TIERS = [(0, 99, 128), (100, 199, 129), (200, 299, 130), (300, 349, None),
            (350, 399, 132), (400, 449, 133), (450, 499, 134), (500, 549, 135),
            (550, 9999, 136)]
# counting stats: SportsData field -> ESPN stat id
COUNTING = {"Sacks": 99, "Interceptions": 95, "FumblesRecovered": 96,
            "Safeties": 98, "BlockedKicks": 97, "FumblesForced": 106}
TD_FIELDS = ["DefensiveTouchdowns", "SpecialTeamsTouchdowns", "InterceptionReturnTouchdowns",
             "FumbleReturnTouchdowns", "BlockedKickReturnTouchdowns",
             "KickReturnTouchdowns", "PuntReturnTouchdowns"]

TEAM_NAMES = {
    "ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills", "CAR": "Panthers",
    "CHI": "Bears", "CIN": "Bengals", "CLE": "Browns", "DAL": "Cowboys", "DEN": "Broncos",
    "DET": "Lions", "GB": "Packers", "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars",
    "KC": "Chiefs", "LV": "Raiders", "LAC": "Chargers", "LAR": "Rams", "MIA": "Dolphins",
    "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants", "NYJ": "Jets",
    "PHI": "Eagles", "PIT": "Steelers", "SF": "49ers", "SEA": "Seahawks", "TB": "Buccaneers",
    "TEN": "Titans", "WSH": "Commanders",
}
NAME_TO_ABBR = {v: k for k, v in TEAM_NAMES.items()}
NAME_TO_ABBR.update({"Football Team": "WSH", "Redskins": "WSH", "Washington": "WSH"})


# --------------------------------------------------------------------------- #
# 1. This league's real D/ST scale, measured from ESPN box scores
# --------------------------------------------------------------------------- #
def _season_frames(years) -> dict[int, pd.DataFrame]:
    out: dict[int, pd.DataFrame] = {}
    for yr in years:
        p = ROOT / "data" / "raw" / "espn" / str(yr) / "box_players.csv"
        if not p.exists():
            continue
        b = pd.read_csv(p)
        b = b[b["position"].astype(str).str.upper().isin(["D/ST", "DST"])]
        b = b[~b["is_playoff"].astype(str).str.lower().eq("true")]
        g = (b.groupby("player_name", as_index=False)
               .agg(ppw=("points", "mean"), wks=("points", "size")))
        if len(g):
            out[yr] = g.sort_values("ppw", ascending=False).reset_index(drop=True)
    return out


def league_curve(years: range = range(2019, 2026)) -> tuple[dict[int, float], float, float]:
    """(rank -> mean points/week, overall mean ppw, persistence rho).

    ESPN box rows only exist for weeks a defense was actually rostered, so we work
    in points-PER-WEEK. Season totals would be the wrong unit twice over: they
    penalise a defense streamed in for six good weeks, and they correlate +0.87
    with weeks-rostered, which manufactures a persistence signal that isn't real."""
    frames = _season_frames(years)
    if not frames:
        raise RuntimeError("no ESPN box data found — cannot calibrate D/ST")

    ranked = {yr: g[g["wks"] >= MIN_WEEKS].reset_index(drop=True) for yr, g in frames.items()}
    ranked = {yr: g for yr, g in ranked.items() if len(g)}
    depth = max(len(g) for g in ranked.values())
    curve = {}
    for k in range(depth):
        vals = [g["ppw"].iloc[k] for g in ranked.values() if k < len(g)]
        if vals:
            curve[k + 1] = sum(vals) / len(vals)
    mean_ppw = float(pd.concat([g["ppw"] for g in ranked.values()]).mean())

    # Persistence, pooled across every consecutive-year pair rather than averaging
    # six noisy per-transition correlations (they range -0.69..+0.72 on n<=20).
    tot = pd.concat([g.assign(year=yr) for yr, g in frames.items()])
    piv = tot[tot["wks"] >= RHO_MIN_WEEKS].pivot_table(
        index="player_name", columns="year", values="ppw")
    yrs = sorted(frames)
    pairs = []
    for a, b_ in zip(yrs, yrs[1:]):
        if a in piv.columns and b_ in piv.columns:
            pr = piv[[a, b_]].dropna()
            if len(pr):
                pairs.append(pr.set_axis(["y0", "y1"], axis=1))
    rho = 0.15
    if pairs:
        allp = pd.concat(pairs)
        if len(allp) >= 20:
            rho = float(max(0.0, allp["y0"].corr(allp["y1"])))
    return curve, mean_ppw, rho


# --------------------------------------------------------------------------- #
# 2. Order the 32 defenses by their projection, scored in league rules
# --------------------------------------------------------------------------- #
def fetch_projections(season: str = "2026REG") -> list[dict]:
    r = requests.get(
        f"https://api.sportsdata.io/v3/nfl/projections/json/FantasyDefenseProjectionsBySeason/{season}",
        params={"key": KEY}, timeout=30)
    r.raise_for_status()
    return r.json()


def _rescale(vals: list[float], real_mean: float, real_sd: float) -> list[float]:
    """Match a miscalibrated projection distribution onto the real NFL one,
    preserving each team's relative standing."""
    n = len(vals)
    mu = sum(vals) / n
    sd = (sum((v - mu) ** 2 for v in vals) / n) ** 0.5
    if sd == 0:
        return [real_mean] * n
    return [real_mean + (v - mu) * (real_sd / sd) for v in vals]


def _tier_points(value: float, tiers, scoring: dict[int, float]) -> float:
    for lo, hi, sid in tiers:
        if lo <= value <= hi:
            return scoring.get(sid, 0.0) if sid is not None else 0.0
    return 0.0


def _expected_tier(mean_pg: float, sd_pg: float, tiers, scoring: dict[int, float]) -> float:
    """Expected per-game tier points for a defense averaging `mean_pg`, integrating
    over a normal spread of weekly outcomes rather than snapping to a single tier."""
    from statistics import NormalDist
    nd = NormalDist(mean_pg, sd_pg)
    exp = 0.0
    for lo, hi, sid in tiers:
        pts = scoring.get(sid, 0.0) if sid is not None else 0.0
        if not pts:
            continue
        p = nd.cdf(hi + 0.5) - nd.cdf(lo - 0.5)
        exp += pts * p
    return exp


def model_scores(rows: list[dict], scoring: dict[int, float]) -> list[dict]:
    """League-rules score per defense. Used for ORDERING only — see module docstring."""
    games = [float(r.get("Games") or WEEKS) or WEEKS for r in rows]
    pa_pg = _rescale([float(r.get("PointsAllowed") or 0) / g for r, g in zip(rows, games)],
                     *NFL_REAL["pa_pg"])
    ya_pg = _rescale([float(r.get("OffensiveYardsAllowed") or 0) / g for r, g in zip(rows, games)],
                     *NFL_REAL["ya_pg"])
    out = []
    for r, g, pa, ya in zip(rows, games, pa_pg, ya_pg):
        counting = sum(scoring.get(sid, 0.0) * float(r.get(f) or 0) for f, sid in COUNTING.items())
        tds = sum(float(r.get(f) or 0) for f in TD_FIELDS)
        # weekly spread around each team's own mean (NFL-typical, not team-specific)
        tier = (_expected_tier(pa, 10.0, PA_TIERS, scoring)
                + _expected_tier(ya, 80.0, YA_TIERS, scoring)) * g
        out.append({**r, "_score": counting + 6.0 * tds + tier, "_pa_pg": pa, "_ya_pg": ya})
    out.sort(key=lambda x: -x["_score"])
    return out


# --------------------------------------------------------------------------- #
# 3. Board rows
# --------------------------------------------------------------------------- #
# A 10-team/16-round draft is 160 picks, but SportsData reports D/ST ADPs like
# 333 and 438 (their board is a different format, and undrafted defenses get a
# sentinel). Never emit those, and never emit a null: the frontend's
# `Math.max(1, Math.round(adp || 0))` turns a missing ADP into ADP 1, which would
# display the Saints defense as a first-round pick.
MAX_SANE_ADP = 240


def _sane_adp(adp, rank: int) -> float:
    """Usable ADP for a defense, falling back to a plausible late-round slot."""
    try:
        v = float(adp)
    except (TypeError, ValueError):
        v = 0.0
    if 0 < v <= MAX_SANE_ADP:
        return round(v, 1)
    return float(130 + 3 * rank)      # undrafted/garbage -> late but ordered


def build(season: str = "2026REG", scoring_year: int = 2026) -> list[dict]:
    """32 D/ST rows shaped like build_board's player dicts, in league points."""
    try:
        from scoring import DST_SLOT, load_scoring
    except ImportError:  # pragma: no cover
        from models.scoring import DST_SLOT, load_scoring
    # D/ST scores on its OWN column: sacks 1.0 (not the IDP 2.0), tackles/PD zeroed,
    # PA/YA tiers live only here. See scoring.load_scoring's docstring.
    scoring = load_scoring(scoring_year, slot=DST_SLOT)
    curve, mean_ppw, rho = league_curve()
    ordered = model_scores(fetch_projections(season), scoring)

    out = []
    for i, r in enumerate(ordered, start=1):
        raw_ppw = curve.get(i, curve[max(curve)])          # empirical kth-best defense
        ppw = mean_ppw + rho * (raw_ppw - mean_ppw)        # regress toward the mean
        team = str(r.get("Team") or "").upper()
        out.append({
            "playerId": f"DST-{team}",
            "name": f"{TEAM_NAMES.get(team, team)} D/ST",
            "team": team,
            "position": "DST",
            "points": round(ppw * WEEKS, 2),
            "adp": _sane_adp(r.get("AverageDraftPositionPPR") or r.get("AverageDraftPosition"), i),
        })
    return out


if __name__ == "__main__":
    curve, mean_ppw, rho = league_curve()
    print("This league's real D/ST scale (ESPN box, 2019-2025):")
    print(f"  league mean {mean_ppw:.2f} pts/week   year-over-year persistence rho = {rho:.3f}")
    for k in (1, 2, 3, 5, 8, 10, 11, 12, 15):
        if k in curve:
            shrunk = mean_ppw + rho * (curve[k] - mean_ppw)
            print(f"  DST{k:<3} raw {curve[k]:5.2f} ppw -> shrunk {shrunk:5.2f} ppw "
                  f"({shrunk * WEEKS:6.1f} season pts)")
    edge = rho * (curve[1] - curve[11]) * WEEKS
    print(f"\n  raw DST1-DST11 spread : {(curve[1] - curve[11]) * WEEKS:5.1f} pts/season")
    print(f"  DRAFTABLE edge (x rho): {edge:5.1f} pts/season  <- what DST1 is actually worth")

    rows = build()
    print(f"\n2026 D/ST board rows ({len(rows)}):")
    print(f"  {'#':>3} {'DEFENSE':<20} {'TEAM':<5} {'LEAGUE PTS':>11} {'ADP':>7}")
    for i, r in enumerate(rows[:12], 1):
        adp = f"{r['adp']:.1f}" if r["adp"] else "—"
        print(f"  {i:>3} {r['name']:<20} {r['team']:<5} {r['points']:>11.1f} {adp:>7}")
    print(f"  ... worst: {rows[-1]['name']} ({rows[-1]['points']:.1f})")
