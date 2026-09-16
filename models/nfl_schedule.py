"""Full-season NFL schedule from ESPN's FREE scoreboard API (no key).

Powers the SeasonIQ planner: bye-week crunch forecasting (who on my roster
sits idle in each of the next few fantasy weeks) and the playoff panel (each
held player's NFL opponent in fantasy weeks 15-17 — the only three weeks that
decide the title). One call per NFL week, cached 7 days — schedules barely
move in-season.

Run `python models/nfl_schedule.py` to refresh and self-test.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
TTL = 7 * 24 * 3600
SB = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
      "?seasontype=2&week={week}&dates={season}")
# normalize to the board's codes (same trap week1_odds hit: WSH/JAC drop teams)
NORM = {"WSH": "WAS", "JAC": "JAX", "LA": "LAR"}


def _cache_path(season: int) -> Path:
    return ROOT / "data" / "processed" / f"nfl_schedule_{season}.json"


def fetch(season: int = 2026, weeks: range = range(1, 19)) -> dict:
    """{week(str): {TEAM: OPP}} — a team absent from its week's map is on BYE."""
    out: dict = {}
    for wk in weeks:
        r = requests.get(SB.format(week=wk, season=season), timeout=15)
        r.raise_for_status()
        games = (r.json().get("events") or [])
        wkmap: dict = {}
        for ev in games:
            comps = (ev.get("competitions") or [{}])[0].get("competitors") or []
            if len(comps) != 2:
                continue
            codes = []
            for c in comps:
                ab = str(((c.get("team") or {}).get("abbreviation")) or "").upper()
                codes.append(NORM.get(ab, ab))
            if all(codes):
                wkmap[codes[0]] = codes[1]
                wkmap[codes[1]] = codes[0]
        out[str(wk)] = wkmap
    return out


def get(season: int = 2026, force: bool = False) -> dict:
    p = _cache_path(season)
    if not force and p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - blob.get("fetchedAt", 0) < TTL and blob.get("weeks"):
                return blob["weeks"]
        except Exception:
            pass
    try:
        weeks = fetch(season)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"fetchedAt": time.time(), "season": season,
                                 "weeks": weeks}), encoding="utf-8")
        return weeks
    except Exception as e:
        if p.exists():
            try:
                blob = json.loads(p.read_text(encoding="utf-8"))
                if blob.get("weeks"):
                    print(f"nfl_schedule: live fetch failed ({e}) — cached copy")
                    return blob["weeks"]
            except Exception:
                pass
        print(f"nfl_schedule: unavailable ({e})")
        return {}


def opponent(sched: dict, team: str, week: int) -> str | None:
    """OPP code, or None = bye (or unknown team code)."""
    t = str(team or "").upper()
    t = NORM.get(t, t)
    return (sched.get(str(week)) or {}).get(t)


if __name__ == "__main__":
    s = get(force=True)
    assert len(s) >= 17, f"expected 18 weeks, got {len(s)}"
    n_teams = [len(v) for v in s.values()]
    print(f"weeks: {len(s)} · teams per week: min {min(n_teams)} max {max(n_teams)}")
    byes = {wk: sorted(set(sum(([a] for a in wkmap), [])) ^
                       set(next(iter(s.values())).keys()))
            for wk, wkmap in s.items() if len(wkmap) < 32}
    for wk in sorted(s, key=int):
        m = s[wk]
        if len(m) < 32:
            all_teams = {t for w in s.values() for t in w}
            print(f"  wk {wk}: byes {sorted(all_teams - set(m))}")
    assert all(n % 2 == 0 for n in n_teams), "odd team count in a week"
    print("SELF-TEST PASSED")
