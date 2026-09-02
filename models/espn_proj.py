"""IDP + K projections straight from ESPN, already scored in THIS league's rules.

Why: SportsData's 2026 IDP projections have been broken since 2026-07-28 — every
established starting LB projects 0.0 tackles and the league-wide max sack total is
~1.0, so `build_board` (correctly) refuses to rebuild from them. That left IDP
frozen on a stale pull.

ESPN is the better source here anyway. We PLAY on ESPN, and `projected_total_points`
comes back already evaluated under our league's exact scoring config — including the
position-scoped IDP rules (2026-08-11 commish ruling: total tackle 1.0 FLAT — the
solo/asst double-pay items were REMOVED from the config — PD 1.5, sacks 2.0) that our
own re-derivation kept getting wrong. No re-scoring, no stat mapping, no double-count
risk: whenever the commish touches scoring, a force-refresh reprices automatically.

KICKERS joined the casualty list on 2026-08-10: the same SportsData feed rot zeroed
every FG *distance split* (all kickers: 0.0 across 0-19/20-29/30-39/40-49/50+), which
is exactly what our scoring ladder consumes — kickers collapsed to ~20 board points
and the mock-draft grade flagged a phantom "K gap". Same cure: ESPN projects kickers
under the league's real FG ladder (FG0 3 / FG40 4 / FG50 5 / miss −1 / PAT 1).

Cached to data/processed/espn_idp_proj_<season>.json (24h TTL, serves stale on
failure) so draft night never blocks on ESPN.

Run `python models/espn_proj.py` to refresh and self-test.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TTL = 24 * 3600
IDP_POS = ("LB", "DE", "DT", "CB", "S")     # ESPN's individual-defender positions


def _cache(season: int, kind: str = "idp") -> Path:
    # guest league gets its own cache files (its appliedTotals are scored in ITS rules)
    lid = os.getenv("DRAFTIQ_LEAGUE_ID")
    suffix = f"_{lid}" if lid else ""
    return ROOT / "data" / "processed" / f"espn_{kind}_proj_{season}{suffix}.json"


def _league(season: int):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT / "scripts"))
    from pull_espn import load_league, normalize_cookies
    s2, swid = normalize_cookies(os.getenv("ESPN_S2"), os.getenv("ESPN_SWID"))
    return load_league(os.getenv("DRAFTIQ_LEAGUE_ID") or os.getenv("ESPN_LEAGUE_ID"),
                       season, s2, swid)


def fetch(season: int = 2026, positions: tuple = IDP_POS) -> list[dict]:
    """Every player ESPN projects at `positions`, with league-scored season points."""
    lg = _league(season)
    seen, out = set(), []
    # Pull per position: free_agents() caps its page size, so one big call silently
    # truncates (1500 back on a 1500 request) and would drop real defenders.
    for pos in positions:
        try:
            players = lg.free_agents(size=400, position=pos)
        except Exception:
            continue
        for p in players:
            pid = getattr(p, "playerId", None)
            proj = getattr(p, "projected_total_points", None) or 0
            # Trust the player's OWN position, not the query filter. Two-way players
            # (e.g. Travis Hunter, CB-eligible but listed WR) come back from a CB
            # query and would otherwise enter the board as a skill player carrying an
            # IDP-inflated projection — he ranked #1 overall with 251 pts before this.
            if getattr(p, "position", None) not in positions:
                continue
            if pid is None or pid in seen or proj <= 0:
                continue
            seen.add(pid)
            out.append({
                "playerId": pid,
                "name": getattr(p, "name", None),
                "position": getattr(p, "position", None),
                "team": getattr(p, "proTeam", None),
                "points": round(float(proj), 2),
            })
    if not out:
        raise RuntimeError(f"ESPN returned no projections for {positions}")
    out.sort(key=lambda r: -r["points"])
    return out


def _get_kind(season: int, kind: str, positions: tuple, force: bool) -> list[dict]:
    p = _cache(season, kind)
    if not force and p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - blob.get("fetchedAt", 0) < TTL and blob.get("players"):
                return blob["players"]
        except Exception:
            pass
    try:
        players = fetch(season, positions)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"fetchedAt": time.time(), "season": season,
                                 "players": players}), encoding="utf-8")
        return players
    except Exception as e:
        if p.exists():
            try:
                blob = json.loads(p.read_text(encoding="utf-8"))
                if blob.get("players"):
                    print(f"espn_proj[{kind}]: live fetch failed ({e}) — using cached copy")
                    return blob["players"]
            except Exception:
                pass
        print(f"espn_proj[{kind}]: unavailable ({e})")
        return []


def get(season: int = 2026, force: bool = False) -> list[dict]:
    """IDP projections (league-scored)."""
    return _get_kind(season, "idp", IDP_POS, force)


def get_k(season: int = 2026, force: bool = False) -> list[dict]:
    """Kicker projections (league-scored — the full FG distance ladder)."""
    return _get_kind(season, "k", ("K",), force)


# ---- IDP MARKET ADP (this league IS the market) -----------------------------
# No national ADP exists for a 1-DP league, so the market price comes from the
# room's own 7 draft histories (2019-2025, scripts analysis 2026-08-09):
#   first IDP off the board: picks 106-134 (rd 9-12 of 12-team), mean pick 123
#   k-th IDP round-anchored to 10 teams: IDP1 ~pick 104 ... IDP10 ~pick 128
#   the season's TOP IDP was drafted pick 115-185 or went entirely undrafted.
# (The curve was shifted ~1 round early as a hedge while the solo/asst tackle
# double-pay was live and inflating what the room saw. The commish removed it
# 2026-08-11 — tackles now 1.0 flat — so the room sees normal values again; the
# early shift stays as a cheap hedge since our strike is the 90/91 wheel anyway.)
# This feeds the SAME survival/urgency machinery as every other position —
# value stays real; the market price is what makes the engine wait.
_MKT_CURVE = [95, 99, 103, 105, 107, 110, 114, 118, 122, 125, 128, 131]


def market_adp(rank: int) -> float:
    """Market pick for the rank-th IDP (1-based) in THIS room, 10-team scale."""
    if rank <= len(_MKT_CURVE):
        return float(_MKT_CURVE[rank - 1])
    return float(min(220, _MKT_CURVE[-1] + 4 * (rank - len(_MKT_CURVE))))


def as_board_rows(season: int = 2026) -> list[dict]:
    """Shaped like build_board's player dicts, with the league-derived market ADP
    attached (rows come back sorted by projected points, so rank = order)."""
    return [{"playerId": f"ESPN-{r['playerId']}", "name": r["name"], "team": r["team"],
             "position": r["position"], "points": r["points"], "adp": market_adp(i)}
            for i, r in enumerate(get(season), start=1)]


def k_board_rows(season: int = 2026) -> list[dict]:
    """Kicker board rows. ADP stays None — kickers are a final-round timing pick
    (r=+0.114 on any signal), and the STREAM logic already handles the timing."""
    return [{"playerId": f"ESPN-{r['playerId']}", "name": r["name"], "team": r["team"],
             "position": "K", "points": r["points"], "adp": None}
            for r in get_k(season)]


if __name__ == "__main__":
    kk = get_k(force=True)
    print(f"ESPN K projections: {len(kk)} kickers")
    for r in kk[:5]:
        print(f"  {str(r['name'])[:24]:<24}{str(r['team']):<5}{r['points']:>8.1f}")
    kpts = [r["points"] for r in kk]
    assert len(kk) >= 30 and max(kpts) > 120, \
        f"kicker projections implausible (n={len(kk)}, max={max(kpts) if kpts else 0})"
    print("  K SELF-TEST PASSED\n")

    rows = get(force=True)
    print(f"ESPN IDP projections: {len(rows)} players")
    print(f"  {'PLAYER':<26}{'POS':<5}{'TM':<5}{'LEAGUE PTS':>11}")
    for r in rows[:14]:
        print(f"  {str(r['name'])[:26]:<26}{str(r['position']):<5}{str(r['team']):<5}{r['points']:>11.1f}")
    from collections import Counter
    print(f"  by position: {dict(Counter(r['position'] for r in rows))}")
    pts = [r["points"] for r in rows]
    print(f"  points: max {max(pts):.0f} · median {sorted(pts)[len(pts)//2]:.0f} · min {min(pts):.0f}")
    assert len(rows) > 300, "expected several hundred projected defenders"
    assert max(pts) > 150, "top IDP should clear 150 pts (1pt-flat tackles, PD 1.5, SK 2)"
    top_lb = [r for r in rows[:20] if r["position"] == "LB"]
    assert len(top_lb) >= 12, "the top of an IDP board in a tackle-heavy league must be LBs"
    print("SELF-TEST PASSED")
