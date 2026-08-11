"""Player headshots for the DraftIQ board.

Why this module exists: the SportsData.io Headshots feed is NOT in this account's
plan — every URL field comes back as the literal string "Scrambled" — and their
`cdn.sportsdata.io/headshots/nfl/low/{id}.png` path now 404s. So we resolve images
from ESPN's public team-roster API instead, which is free, needs no key, is the
freshest source (it updates on trades/signings), and covers ~99% of our board.

Shape: one sweep of the 32 team rosters -> {normalized name: {id, url, team}}.
The result is cached in memory AND on disk (data/processed/headshots_espn.json)
so draft night still renders faces if ESPN is unreachable when the app boots.
"""
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import requests

ESPN_TEAMS = [
    "ari", "atl", "bal", "buf", "car", "chi", "cin", "cle", "dal", "den", "det",
    "gb", "hou", "ind", "jax", "kc", "lv", "lac", "lar", "mia", "min", "ne",
    "no", "nyg", "nyj", "phi", "pit", "sf", "sea", "tb", "ten", "wsh",
]

# SportsData (board) abbreviation -> ESPN abbreviation, where they disagree.
TEAM_ALIASES = {
    "WAS": "WSH", "JAC": "JAX", "LVR": "LV", "GBP": "GB", "KCC": "KC",
    "NEP": "NE", "NOS": "NO", "SFO": "SF", "TBB": "TB", "ARZ": "ARI",
    "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "OAK": "LV", "SD": "LAC",
    "STL": "LAR", "LA": "LAR",
}

CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "processed", "headshots_espn.json")
CACHE_TTL = 24 * 3600          # rosters change slowly; one sweep a day is plenty
FETCH_TIMEOUT = 15

_mem = {"fetched_at": 0, "players": {}}   # players: normname -> {id, url, team, pos, name}

_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(s):
    """'Ja'Marr Chase' / 'Marvin Harrison Jr.' / 'José Ramírez' -> comparable key."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", "and")
    s = _SUFFIXES.sub("", s)
    return re.sub(r"[^a-z]", "", s)


def norm_team(t):
    t = (t or "").strip().upper()
    return TEAM_ALIASES.get(t, t)


def team_logo(team):
    """ESPN team logo — used for D/ST rows, which have no player headshot."""
    t = norm_team(team)
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{t.lower()}.png" if t else None


def _fetch_team(abbr):
    url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{abbr}/roster"
    resp = requests.get(url, timeout=FETCH_TIMEOUT)
    resp.raise_for_status()
    out = []
    for group in resp.json().get("athletes", []):
        for a in group.get("items", []):
            href = (a.get("headshot") or {}).get("href")
            if not href:
                continue
            out.append({
                "id": str(a.get("id") or ""),
                "name": a.get("displayName"),
                "team": abbr.upper(),
                "pos": ((a.get("position") or {}).get("abbreviation") or "").upper(),
                "url": href,
            })
    return out


def _load_disk():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            blob = json.load(f)
        if isinstance(blob.get("players"), dict):
            return blob
    except Exception:
        pass
    return None


def _save_disk(blob):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(blob, f)
    except Exception:
        pass          # a cold cache is survivable; a crashed board is not


def _sweep():
    """Fetch all 32 rosters -> {normname: record}. Raises if ESPN is unreachable."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        chunks = list(ex.map(_fetch_team, ESPN_TEAMS))
    players = {}
    for chunk in chunks:
        for p in chunk:
            # first writer wins; the roster feeds are already team-scoped so
            # duplicate normalized names across teams are rare and low-stakes.
            players.setdefault(norm_name(p["name"]), p)
    if not players:
        raise RuntimeError("ESPN roster sweep returned no players")
    return players


def get_map(force=False):
    """Cached {normname: record}. Falls back to the on-disk copy if ESPN fails."""
    now = time.time()
    if not force and _mem["players"] and (now - _mem["fetched_at"] < CACHE_TTL):
        return _mem["players"]

    if not force:
        disk = _load_disk()
        if disk and (now - disk.get("fetched_at", 0) < CACHE_TTL):
            _mem.update(disk)
            return _mem["players"]

    try:
        players = _sweep()
        _mem.update({"fetched_at": now, "players": players})
        _save_disk({"fetched_at": now, "players": players})
    except Exception as e:
        stale = _load_disk() or {}
        if stale.get("players"):
            print(f"headshots: ESPN sweep failed ({e}) — using cached copy "
                  f"({len(stale['players'])} players)")
            _mem.update(stale)
        else:
            print(f"headshots: ESPN sweep failed ({e}) — no cache, going without")
            _mem.update({"fetched_at": now, "players": {}})
    return _mem["players"]


def resolve(name, team=None, pos=None, players=None):
    """Best headshot URL for a board row (D/ST -> team logo). None if unknown."""
    p = (pos or "").upper().replace("/", "").replace(".", "")
    if p in ("DST", "DEF", "D"):
        return team_logo(team or name)
    players = get_map() if players is None else players
    rec = players.get(norm_name(name))
    return rec["url"] if rec else None


def attach(rows, name_key="name", team_key="team", pos_key="position", out_key="headshot"):
    """Stamp `out_key` onto each row in place; returns (matched, total)."""
    players = get_map()
    matched = 0
    for r in rows:
        url = resolve(r.get(name_key), r.get(team_key), r.get(pos_key), players=players)
        r[out_key] = url
        if url:
            matched += 1
    return matched, len(rows)


if __name__ == "__main__":     # python headshots.py -> sanity check the sweep
    t0 = time.time()
    m = get_map(force=True)
    print(f"ESPN headshots: {len(m)} players in {time.time() - t0:.1f}s")
    for who in ("Bijan Robinson", "Ja'Marr Chase", "Marvin Harrison Jr.", "Puka Nacua"):
        print(f"  {who:<22} {resolve(who, players=m)}")
    print(f"  {'D/ST (KC)':<22} {resolve('Chiefs', 'KC', 'DST')}")
