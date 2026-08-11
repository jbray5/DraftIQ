"""WEEK 1 MATCHUP CONTEXT for the streaming positions (D/ST and K).

Why this exists: the defense you draft in round 16 is a WEEK 1 STARTER, not a
season-long asset. Measured on 7 seasons of this league (2019-2025):

  * D/ST season-long identity is worthless — week-to-week persistence is +0.001,
    and the best defense is worth only ~10 pts of draft VORP over replacement.
  * But the WEEKLY MATCHUP is strongly predictive. The implied opponent total,
    (over_under + team_spread) / 2, correlates -0.294 with D/ST fantasy points,
    a cleanly monotone 9.56 -> 4.08 pts/wk from the best to the worst quintile.
    Streaming on it beats a coin flip by ~+55 pts/season and beats ESPN's own
    weekly projection (r +0.223).
  * Kickers are NOT the same story. Their best Vegas feature (implied OWN team
    total) manages only r=+0.114 and +6 pts/season over a coin flip, so we expose
    the number for tie-breaking but deliberately do NOT rank kickers by it.

Data source is ESPN's free public API — no key. SportsData is unusable here: it
returns 401 for 2019-2024 and silently CORRUPTED values for 2025 (its own two
endpoints disagree on the same game; every over/under that week reads ~20 when
reality was ~44).

Cached to data/processed/week1_odds_<season>.json (6h TTL, serves stale on
network failure) so draft night never blocks on a third party.

Run `python models/week1_odds.py` to refresh and print the Week 1 board.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ODDS = ("https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
        "events/{eid}/competitions/{eid}/odds")
TTL = 6 * 3600
TIMEOUT = 20

# Normalize to the abbreviations THE BOARD uses — verified against board_2026.csv:
# all 32 D/ST and K rows use JAX and WAS. ESPN's scoreboard says WSH; some feeds
# say JAC. Getting this wrong silently drops teams from the join.
TEAM_FIX = {"WSH": "WAS", "JAC": "JAX"}
ALIASES = {"LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


def norm_team(t: str) -> str:
    t = str(t or "").upper().strip()
    t = TEAM_FIX.get(t, t)
    return ALIASES.get(t, t)


def _cache_path(season: int) -> Path:
    return ROOT / "data" / "processed" / f"week1_odds_{season}.json"


def _pregame_line(eid: str) -> dict | None:
    """Best PREGAME spread/total for an event.

    Providers whose name contains 'Live Odds' are in-game feeds — they encode the
    result and would leak badly into any backtest, so they are excluded by name.
    """
    try:
        r = requests.get(ODDS.format(eid=eid), timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        items = [i for i in (r.json().get("items") or [])
                 if "live odds" not in str((i.get("provider") or {}).get("name", "")).lower()]
        if not items:
            return None
        # prefer a consensus/market line, else the first real book
        pref = ("consensus", "espn bet", "draftkings", "caesars")
        items.sort(key=lambda i: next(
            (n for n, p in enumerate(pref)
             if p in str((i.get("provider") or {}).get("name", "")).lower()), 99))
        it = items[0]
        spread, ou = it.get("spread"), it.get("overUnder")
        if spread is None or ou is None:
            return None
        return {"spread_home": float(spread), "over_under": float(ou),
                "provider": (it.get("provider") or {}).get("name"),
                "details": it.get("details")}
    except Exception:
        return None


def fetch(season: int = 2026, week: int = 1) -> dict:
    """{teams:{ABBR:{...}}, games:[...]} for one week. Raises on total failure."""
    r = requests.get(SCOREBOARD, params={"dates": season, "seasontype": 2, "week": week},
                     timeout=TIMEOUT)
    r.raise_for_status()
    events = r.json().get("events") or []
    if not events:
        raise RuntimeError(f"no {season} week {week} games scheduled yet")

    ids = [e["id"] for e in events]
    with ThreadPoolExecutor(max_workers=8) as ex:
        lines = dict(zip(ids, ex.map(_pregame_line, ids)))

    teams, games = {}, []
    for e in events:
        comp = (e.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        home = norm_team(((sides.get("home") or {}).get("team") or {}).get("abbreviation"))
        away = norm_team(((sides.get("away") or {}).get("team") or {}).get("abbreviation"))
        ln = lines.get(e["id"]) or {}
        sh, ou = ln.get("spread_home"), ln.get("over_under")
        g = {"eventId": e["id"], "date": (e.get("date") or "")[:10], "home": home, "away": away,
             "spreadHome": sh, "overUnder": ou, "provider": ln.get("provider"),
             "details": ln.get("details")}
        games.append(g)
        for side, team, opp in (("home", home, away), ("away", away, home)):
            ts = None if sh is None else (sh if side == "home" else -sh)
            teams[team] = {
                "team": team, "opp": opp, "isHome": side == "home", "date": g["date"],
                "spread": ts, "overUnder": ou,
                # a NEGATIVE team spread means favored, so own total subtracts it
                "impliedTeamTotal": None if (ts is None or ou is None) else round((ou - ts) / 2, 2),
                "impliedOppTotal": None if (ts is None or ou is None) else round((ou + ts) / 2, 2),
                "provider": ln.get("provider"),
            }
    # teams on bye / not scheduled simply won't appear
    return {"season": season, "week": week, "fetchedAt": time.time(),
            "games": games, "teams": teams}


def get(season: int = 2026, week: int = 1, force: bool = False) -> dict:
    """Cached fetch. Falls back to the stale cache if ESPN is unreachable."""
    p = _cache_path(season)
    if not force and p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if blob.get("week") == week and time.time() - blob.get("fetchedAt", 0) < TTL:
                return blob
        except Exception:
            pass
    try:
        blob = fetch(season, week)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(blob), encoding="utf-8")
        return blob
    except Exception as e:
        if p.exists():
            try:
                stale = json.loads(p.read_text(encoding="utf-8"))
                stale["stale"] = True
                stale["error"] = str(e)
                print(f"week1_odds: live fetch failed ({e}) — serving cached copy")
                return stale
            except Exception:
                pass
        print(f"week1_odds: unavailable ({e})")
        return {"season": season, "week": week, "games": [], "teams": {}, "error": str(e)}


def dst_ranks(season: int = 2026, week: int = 1) -> dict:
    """{TEAM: {...matchup..., w1Rank, w1Tier}} ranked by implied OPPONENT total (asc).

    Rank 1 = faces the offense Vegas expects to score least. This is the number
    that should drive which defense you draft in the last rounds."""
    blob = get(season, week)
    teams = {k: dict(v) for k, v in (blob.get("teams") or {}).items()
             if v.get("impliedOppTotal") is not None}
    order = sorted(teams.values(), key=lambda t: t["impliedOppTotal"])
    n = len(order) or 1
    for i, t in enumerate(order, start=1):
        t["w1Rank"] = i
        # quintiles mirror the backtest bins: tier 1 = best matchup quintile
        t["w1Tier"] = min(5, 1 + (i - 1) * 5 // n)
    return {t["team"]: t for t in order}


def k_context(season: int = 2026, week: int = 1) -> dict:
    """{TEAM: {...}} with implied OWN total. Exposed for tie-breaking only —
    kicker scoring is barely predictable (r=+0.114, ~+6 pts/season)."""
    blob = get(season, week)
    teams = {k: dict(v) for k, v in (blob.get("teams") or {}).items()
             if v.get("impliedTeamTotal") is not None}
    order = sorted(teams.values(), key=lambda t: -t["impliedTeamTotal"])
    n = len(order) or 1
    for i, t in enumerate(order, start=1):
        t["w1Rank"] = i
        t["w1Tier"] = min(5, 1 + (i - 1) * 5 // n)
    return {t["team"]: t for t in order}


if __name__ == "__main__":
    blob = get(force=True)
    games = blob.get("games", [])
    withline = [g for g in games if g.get("overUnder") is not None]
    print(f"{blob['season']} week {blob['week']}: {len(games)} games, "
          f"{len(withline)} with a posted line "
          f"(providers: {sorted({g.get('provider') for g in withline if g.get('provider')})})")

    d = dst_ranks()
    print(f"\nD/ST — WEEK 1 STREAMING BOARD (rank by implied OPPONENT total, lower = better)")
    print(f"  {'#':>3} {'DEF':<5} {'OPP':<5} {'H/A':<4} {'OPP TOTAL':>10} {'SPREAD':>8}  TIER")
    for t in list(d.values())[:12]:
        print(f"  {t['w1Rank']:>3} {t['team']:<5} {t['opp']:<5} "
              f"{'HOME' if t['isHome'] else 'away':<4} {t['impliedOppTotal']:>10.1f} "
              f"{t['spread']:>+8.1f}  T{t['w1Tier']}")
    worst = list(d.values())[-3:]
    print("  ...worst matchups: " + ", ".join(
        f"{t['team']} vs {t['opp']} ({t['impliedOppTotal']:.1f})" for t in worst))

    k = k_context()
    print(f"\nK — week 1 implied OWN team total (tie-breaker only; weak signal)")
    for t in list(k.values())[:8]:
        print(f"  {t['w1Rank']:>3} {t['team']:<5} vs {t['opp']:<5} "
              f"own total {t['impliedTeamTotal']:>5.1f}  O/U {t['overUnder']:.1f}")

    assert len(d) >= 24, "expected most teams to have a week-1 line"
    tot = [t["impliedOppTotal"] for t in d.values()]
    assert 10 < min(tot) and max(tot) < 40, f"implausible implied totals: {min(tot)}-{max(tot)}"
    # every team code must exist on the board, or the join silently drops it
    import csv as _csv
    board_teams = {r["team"] for r in _csv.DictReader(
        open(ROOT / "data/processed/board_2026.csv", encoding="utf-8")) if r["pos"] == "DST"}
    orphans = sorted(set(d) - board_teams)
    assert not orphans, f"team codes not on the board (join would drop them): {orphans}"
    print(f"\nall {len(d)} team codes match the board's D/ST rows")
    print("SELF-TEST PASSED")
