"""Monte Carlo season simulator — live championship odds while you draft.

Answers the only question that matters mid-draft: "did that pick make me more
likely to WIN THE LEAGUE?" After every pick the UI sends all 10 rosters; this
sims the season ~1000× and returns each team's title/playoff odds.

Calibration is from THIS league's history, not guesses:
  * weekly scoring noise = the pooled within-team-season sd of real weekly
    scores, 2019-2025 (data/raw/espn/*/weekly_scores.csv) — how much a fantasy
    team's week actually swings around its own mean.
  * a per-season "draft luck" shock on each team's mean, because projected
    points are not delivered points (injuries, busts, breakouts).

Structure is the real 2026 format: 10 teams, 14-week regular season, 6-team
playoff (top-2 byes), one-week rounds, seeded by record with points-for tiebreak
— from data/raw/espn/2026/settings.json (playoff_team_count: 6).

Mid-draft, unfilled starter slots are filled with a realistic expectation: the
k-th best player still available at that position, where k = half the teams that
still need one (you won't get the best remaining; you won't get the worst).

Pure numpy, ~1000 sims in well under a second. No LLM, no network.
"""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from pick_engine import normalize_pos
except ImportError:  # pragma: no cover
    from models.pick_engine import normalize_pos

ROOT = Path(__file__).resolve().parents[1]

REG_WEEKS = 14
PLAYOFF_TEAMS = 6            # 2026 settings.json: top 2 get byes, 1-week rounds
SEASON_WEEKS = 17            # projections are full-season totals
SEASON_SHOCK = 0.08          # sd of the per-season draft-luck shock, as a share of mean
STARTER_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "IDP", "DST", "K"]
FLEX_ELIG = ("RB", "WR", "TE")

# Guest-league mode: DRAFTIQ_LEAGUE picks another league_config key and rebuilds the
# lineup shape / season structure from it (home league "2026" keeps the values above).
LEAGUE_KEY = os.getenv("DRAFTIQ_LEAGUE", "2026")
if LEAGUE_KEY != "2026":
    try:
        _c = json.loads((ROOT / "data" / "league_config.json").read_text(encoding="utf-8"))[LEAGUE_KEY]
        _S2P = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "DP": "IDP", "D/ST": "DST", "K": "K"}
        STARTER_SLOTS = [_S2P.get(s, s) for s, n in _c["starters"].items()
                         for _ in range(int(n))] + ["FLEX"] * int(_c.get("flex_slots", 0))
        REG_WEEKS = int(_c.get("reg_weeks", REG_WEEKS))
        PLAYOFF_TEAMS = int(_c.get("playoff_teams", PLAYOFF_TEAMS))
    except Exception:
        pass                 # fall back to home-league structure rather than crash


@functools.lru_cache(maxsize=1)
def weekly_sd() -> float:
    """Pooled within-team-season sd of real weekly scores in this league."""
    resids = []
    for yr in range(2019, 2026):
        p = ROOT / "data" / "raw" / "espn" / str(yr) / "weekly_scores.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df = df[df["is_regular"].astype(str).str.lower().eq("true")]
        df = df[~df["is_bye"].astype(str).str.lower().eq("true")]
        df = df[pd.to_numeric(df["points"], errors="coerce").notna()]
        df["points"] = df["points"].astype(float)
        for (_, _), g in df.groupby([df["team_id"], yr * df["team_id"] ** 0]):
            if len(g) >= 6:
                resids.extend((g["points"] - g["points"].mean()).tolist())
    return float(np.std(resids)) if resids else 22.0


def lineup_points(players: list[dict], pts_of) -> tuple[float, list[str]]:
    """Best legal starting lineup's season points + which slots stayed empty.
    `players`: [{name, position}]; `pts_of(name, pos)` -> season league points."""
    by_pos: dict[str, list[float]] = {}
    for p in players or []:
        pos = normalize_pos(p.get("position") or p.get("pos"))
        val = pts_of(p.get("name"), pos)
        by_pos.setdefault(pos, []).append(val)
    for lst in by_pos.values():
        lst.sort(reverse=True)
    used: dict[str, int] = {}
    total, empty = 0.0, []
    flex_pool: list[float] = []
    for slot in STARTER_SLOTS:
        if slot == "FLEX":
            continue
        lst = by_pos.get(slot, [])
        i = used.get(slot, 0)
        if i < len(lst):
            total += lst[i]
            used[slot] = i + 1
        else:
            empty.append(slot)
    for pos in FLEX_ELIG:
        lst = by_pos.get(pos, [])
        flex_pool.extend(lst[used.get(pos, 0):])
    flex_pool.sort(reverse=True)
    n_flex = STARTER_SLOTS.count("FLEX")
    for j in range(n_flex):
        if j < len(flex_pool):
            total += flex_pool[j]
        else:
            empty.append("FLEX")
    return total, empty


def fill_values(team_rosters: dict, board_avail: list[dict]) -> dict[str, float]:
    """Season points a realistic future pick will deliver for each open slot type:
    the k-th best available at the position, k = half the teams still needing it."""
    need_count: dict[str, int] = {}
    for players in team_rosters.values():
        have: dict[str, int] = {}
        for p in players or []:
            pos = normalize_pos(p.get("position") or p.get("pos"))
            have[pos] = have.get(pos, 0) + 1
        _reqs = [(pos, STARTER_SLOTS.count(pos))
                 for pos in ("QB", "RB", "WR", "TE", "IDP", "DST", "K")
                 if STARTER_SLOTS.count(pos)]
        for pos, req in _reqs:
            if have.get(pos, 0) < req:
                need_count[pos] = need_count.get(pos, 0) + (req - have.get(pos, 0))
    by_pos: dict[str, list[float]] = {}
    for r in board_avail:
        pos = normalize_pos(r.get("pos") or r.get("position"))
        try:
            pts = float(r.get("league_pts") or r.get("points") or 0)
        except (TypeError, ValueError):
            pts = 0.0
        by_pos.setdefault(pos, []).append(pts)
    out = {}
    for pos, lst in by_pos.items():
        lst.sort(reverse=True)
        k = max(0, (need_count.get(pos, 2) // 2))
        out[pos] = lst[min(k, len(lst) - 1)] if lst else 0.0
    out["FLEX"] = max(out.get(p, 0.0) for p in FLEX_ELIG) * 0.9 if by_pos else 0.0
    return out


def schedule(n_teams: int, weeks: int = REG_WEEKS) -> list[list[tuple[int, int]]]:
    """Deterministic round-robin (circle method), repeated to fill the season."""
    ids = list(range(n_teams))
    rounds = []
    arr = ids[1:]
    for _ in range(n_teams - 1):
        pairs = [(ids[0], arr[-1])]
        for i in range((n_teams - 2) // 2):
            pairs.append((arr[i], arr[-2 - i]))
        rounds.append(pairs)
        arr = arr[-1:] + arr[:-1]
    return [rounds[w % len(rounds)] for w in range(weeks)]


def title_odds(team_rosters: dict[str, list[dict]], board_avail: list[dict],
               pts_lookup, n_sims: int = 1000, seed: int = 2026) -> dict:
    """{team: {title, playoff, expWins, weeklyMean}} via Monte Carlo.
    `pts_lookup(name, pos)` -> season league points for a rostered player."""
    teams = list(team_rosters.keys())
    n = len(teams)
    fills = fill_values(team_rosters, board_avail)
    means = np.zeros(n)
    for i, t in enumerate(teams):
        total, empty = lineup_points(team_rosters[t], pts_lookup)
        total += sum(fills.get(slot, 0.0) for slot in empty)   # expected future picks
        means[i] = total / SEASON_WEEKS

    sd_w = weekly_sd()
    rng = np.random.default_rng(seed)
    shock = rng.normal(1.0, SEASON_SHOCK, size=(n_sims, n))      # draft luck / injuries
    sim_means = means[None, :] * shock
    sched = schedule(n)
    weekly = rng.normal(sim_means[:, None, :], sd_w,
                        size=(n_sims, REG_WEEKS, n))             # sims × weeks × teams

    wins = np.zeros((n_sims, n), dtype=np.int16)
    for w, pairs in enumerate(sched):
        for a, b in pairs:
            a_w = weekly[:, w, a] > weekly[:, w, b]
            wins[:, a] += a_w
            wins[:, b] += ~a_w
    points_for = weekly.sum(axis=1)

    # seed by record, points-for tiebreak → top-6, byes for 1-2
    order = np.lexsort((-points_for, -wins), axis=1)             # per sim: best first
    titles = np.zeros(n)
    playoffs = np.zeros(n)
    po_sd = sd_w
    for s in range(n_sims):
        seeds = order[s, :PLAYOFF_TEAMS]
        playoffs[seeds] += 1
        m = sim_means[s]

        def game(x, y):
            return x if rng.normal(m[x], po_sd) > rng.normal(m[y], po_sd) else y

        if PLAYOFF_TEAMS == 4:                                    # SF 1v4, 2v3 → final
            f1 = game(seeds[0], seeds[3])
            f2 = game(seeds[1], seeds[2])
        else:                                                     # 6-team, top-2 byes
            w45 = game(seeds[3], seeds[4])                        # QF: 4v5, 3v6
            w36 = game(seeds[2], seeds[5])
            f1 = game(seeds[0], w45)                              # SF vs byes
            f2 = game(seeds[1], w36)
        titles[game(f1, f2)] += 1

    return {t: {"title": round(float(titles[i]) / n_sims, 4),
                "playoff": round(float(playoffs[i]) / n_sims, 4),
                "expWins": round(float(wins[:, i].mean()), 2),
                "weeklyMean": round(float(means[i]), 1)}
            for i, t in enumerate(teams)}


if __name__ == "__main__":     # python models/season_sim.py — self-test
    import csv
    import time
    rows = list(csv.DictReader(open(ROOT / "data/processed/board_2026.csv", encoding="utf-8")))
    by_key = {}
    for r in rows:
        by_key[(r["name"], normalize_pos(r["pos"]))] = float(r["league_pts"] or 0)

    def lookup(name, pos):
        return by_key.get((name, pos), 0.0)

    print(f"calibrated weekly sd from league history: {weekly_sd():.1f} pts")

    teams = ["Gilbert", "Bollinger", "Hubauer", "Putman", "Walker", "Wester", "Spivey", "Street", "Munford", "Ray"]
    # empty draft: everyone should be ~10% title, ~60% playoffs
    empty = {t: [] for t in teams}
    t0 = time.time()
    odds = title_odds(empty, rows, lookup, n_sims=1000)
    print(f"\nempty rosters ({(time.time()-t0)*1000:.0f}ms):"
          f" title range {min(o['title'] for o in odds.values()):.3f}"
          f"-{max(o['title'] for o in odds.values()):.3f} (expect ~0.10 each)")

    # give Ray the top 2 RBs + top WR; opponents get nothing yet
    stacked = {t: [] for t in teams}
    stacked["Ray"] = [{"name": "Bijan Robinson", "position": "RB"},
                       {"name": "Jahmyr Gibbs", "position": "RB"},
                       {"name": "Puka Nacua", "position": "WR"}]
    odds2 = title_odds(stacked, rows, lookup, n_sims=1000)
    print(f"Ray with Bijan+Gibbs+Nacua vs empty league: title {odds2['Ray']['title']:.1%} "
          f"(was {odds['Ray']['title']:.1%}), expWins {odds2['Ray']['expWins']}")
    assert odds2["Ray"]["title"] > odds["Ray"]["title"] + 0.02, "stacked roster must move the needle"
    print("sanity PASSED: elite roster lifts title odds")
