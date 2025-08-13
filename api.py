from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

SPORTSDATA_KEY = os.getenv("SPORTSDATA_BAKER_KEY")
TIMEOUT = 20
TTL_SECONDS = 600  # 10 minutes cache

# ---------------------------
# Simple in-memory cache
# ---------------------------
_cache = {}  # { key: (expiry_ts, data) }


def _get_cached(key, fetch_fn):
    now = time.time()
    if key in _cache:
        exp, data = _cache[key]
        if now < exp:
            return data
    data = fetch_fn()
    _cache[key] = (now + TTL_SECONDS, data)
    return data


# ---------------------------
# External fetchers
# ---------------------------
def _fetch_baker(season: str):
    url = f"https://baker-api.sportsdata.io/baker/v2/nfl/projections/players/full-season/{season}/avg"
    resp = requests.get(url, params={"key": SPORTSDATA_KEY}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fetch_adp_and_points(season: str):
    url = f"https://api.sportsdata.io/v3/nfl/projections/json/PlayerSeasonProjectionStats/{season}"
    resp = requests.get(url, params={"key": SPORTSDATA_KEY}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fetch_headshots():
    url = "https://api.sportsdata.io/v3/nfl/headshots/json/Headshots"
    resp = requests.get(url, params={"key": SPORTSDATA_KEY}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ---------------------------
# Utilities
# ---------------------------
def _norm(s):
    """Normalize strings for matching."""
    return (s or "").strip().lower()


def _norm_team(team):
    """Normalize team code or name to consistent lowercase 3-letter code."""
    if not team:
        return ""
    team_map = {
        "cincinnati": "cin", "cin": "cin",
        "atlanta": "atl", "atl": "atl",
        "philadelphia": "phi", "phi": "phi",
        "new york giants": "nyg", "giants": "nyg",
        "new york jets": "nyj", "jets": "nyj",
        "san francisco": "sf", "sf": "sf",
        "kansas city": "kc", "kc": "kc",
        "buffalo": "buf", "buf": "buf",
        # add more as needed
    }
    t = team.strip().lower()
    return team_map.get(t, t)


def _is_valid_headshot(url):
    """Check if a headshot URL is usable (not placeholder, nophoto, or 0.png)."""
    if not url:
        return False
    lowered = url.lower()
    return (
        "placeholder" not in lowered
        and "nophoto" not in lowered
        and not lowered.endswith("/0.png")
    )


def _pick_points_from_adp_row(row):
    ppr = row.get("FantasyPointsPPR")
    if isinstance(ppr, (int, float)):
        return ppr
    base = row.get("FantasyPoints")
    if isinstance(base, (int, float)):
        return base
    try:
        return float(ppr or base)
    except Exception:
        return None


def _pick_adp_from_adp_row(row):
    adp = row.get("AverageDraftPositionPPR")
    if isinstance(adp, (int, float)):
        return adp
    adp2 = row.get("AverageDraftPosition")
    if isinstance(adp2, (int, float)):
        return adp2
    try:
        return float(adp or adp2)
    except Exception:
        return None


# ---------------------------
# API
# ---------------------------
@app.route("/api/debug-headshots", methods=["GET"])
def debug_headshots():
    try:
        data = _fetch_headshots()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rankings", methods=["GET"])
def rankings():
    season = request.args.get("season", "2025REG")

    try:
        baker_data = _get_cached(f"baker:{season}", lambda: _fetch_baker(season))
        adp_data = _get_cached(f"adp:{season}", lambda: _fetch_adp_and_points(season))
        headshot_data = _get_cached("headshots", _fetch_headshots)
    except Exception as e:
        print("Error fetching data:", e)
        return jsonify([]), 500

    # --- Build ADP/points lookups ---
    adp_by_id = {}
    adp_by_name_team = {}

    for r in adp_data or []:
        pid = str(r.get("PlayerID")) if r.get("PlayerID") is not None else None
        adp_val = _pick_adp_from_adp_row(r)
        pts_val = _pick_points_from_adp_row(r)

        if pid:
            adp_by_id[pid] = {"adp": adp_val, "points": pts_val}

        key = (_norm(r.get("Name")), _norm_team(r.get("Team")))
        adp_by_name_team[key] = {"adp": adp_val, "points": pts_val}

    # --- Build headshot lookups ---
    headshot_by_id = {}
    headshot_by_name_team = {}

    for h in headshot_data or []:
        pid = str(h.get("PlayerID")) if h.get("PlayerID") is not None else None
        url = (
            h.get("PreferredHostedHeadshotUrl")
            or h.get("HostedHeadshotNoBackgroundUrl")
            or h.get("HostedHeadshotWithBackgroundUrl")
        )
        if _is_valid_headshot(url):
            if pid:
                headshot_by_id[pid] = url
            key = (_norm(h.get("Name")), _norm_team(h.get("Team")))
            headshot_by_name_team[key] = url

    # --- Merge into Baker rows ---
    merged = []
    for p in baker_data or []:
        pid = str(p.get("player_id") or p.get("PlayerID") or "").strip() or None
        name = p.get("name") or p.get("Name")
        team = p.get("team") or p.get("Team")
        pos = p.get("position") or p.get("Position")

        name_team_key = (_norm(name), _norm_team(team))

        # ADP/points lookup
        adp_pts = adp_by_id.get(pid) if pid in adp_by_id else adp_by_name_team.get(name_team_key)

        # Headshot lookup — always try both
        headshot_url = None
        if pid and pid in headshot_by_id and _is_valid_headshot(headshot_by_id[pid]):
            headshot_url = headshot_by_id[pid]
        if not _is_valid_headshot(headshot_url) and name_team_key in headshot_by_name_team:
            if _is_valid_headshot(headshot_by_name_team[name_team_key]):
                headshot_url = headshot_by_name_team[name_team_key]

        if not _is_valid_headshot(headshot_url):
            print(f"No valid headshot for: {name} ({team}) pid={pid}")

        merged.append({
            "playerId": pid or p.get("PlayerID"),
            "name": name,
            "team": team,
            "position": pos,
            "adp": (adp_pts or {}).get("adp"),
            "points": (adp_pts or {}).get("points"),
            "headshot": headshot_url if _is_valid_headshot(headshot_url) else None
        })

    # --- Add ADP-only players ---
    seen = set((m["playerId"], _norm(m["name"]), _norm_team(m["team"])) for m in merged)
    for r in adp_data or []:
        pid = str(r.get("PlayerID")) if r.get("PlayerID") is not None else None
        nm = r.get("Name")
        tm = r.get("Team")
        pos = r.get("Position")
        key = (pid, _norm(nm), _norm_team(tm))
        if key in seen:
            continue

        name_team_key = (_norm(nm), _norm_team(tm))

        headshot_url = None
        if pid and pid in headshot_by_id and _is_valid_headshot(headshot_by_id[pid]):
            headshot_url = headshot_by_id[pid]
        if not _is_valid_headshot(headshot_url) and name_team_key in headshot_by_name_team:
            if _is_valid_headshot(headshot_by_name_team[name_team_key]):
                headshot_url = headshot_by_name_team[name_team_key]

        if not _is_valid_headshot(headshot_url):
            print(f"No valid headshot for: {nm} ({tm}) pid={pid}")

        merged.append({
            "playerId": pid,
            "name": nm,
            "team": tm,
            "position": pos,
            "adp": _pick_adp_from_adp_row(r),
            "points": _pick_points_from_adp_row(r),
            "headshot": headshot_url if _is_valid_headshot(headshot_url) else None
        })

    # --- Sort and rank ---
    merged_sorted = sorted(
        merged,
        key=lambda x: (x.get("adp") is None, x.get("adp") if x.get("adp") is not None else float("inf"))
    )

    ranked = []
    for i, p in enumerate(merged_sorted, start=1):
        ranked.append({
            "rank": i,
            "playerId": p.get("playerId"),
            "name": p.get("name"),
            "team": p.get("team"),
            "position": p.get("position"),
            "adp": p.get("adp"),
            "points": p.get("points"),
            "headshot": p.get("headshot")
        })

    return jsonify(ranked)


@app.route("/api/get-ranked-players", methods=["GET"])
def get_ranked_players_alias():
    return rankings()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
