# api.py
from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

SPORTSDATA_KEY = os.getenv("SPORTSDATA_BAKER_KEY")  # reuse your existing env var
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
    """
    Baker projections (broad player list). We still use this for extra coverage
    but NOT for points anymore.
    """
    url = f"https://baker-api.sportsdata.io/baker/v2/nfl/projections/players/full-season/{season}/avg"
    resp = requests.get(url, params={"key": SPORTSDATA_KEY}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fetch_adp_and_points(season: str):
    """
    SportsData.io season projection stats which include:
      - AverageDraftPositionPPR / AverageDraftPosition
      - FantasyPointsPPR / FantasyPoints
    We will use FantasyPointsPPR for `points`.
    """
    url = f"https://api.sportsdata.io/v3/nfl/projections/json/PlayerSeasonProjectionStats/{season}"
    resp = requests.get(url, params={"key": SPORTSDATA_KEY}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ---------------------------
# Utilities
# ---------------------------
def _norm(s):
    return (s or "").strip().lower()


def _pick_points_from_adp_row(row):
    # Your request: points = FantasyPointsPPR (fallback to FantasyPoints)
    ppr = row.get("FantasyPointsPPR")
    if isinstance(ppr, (int, float)):
        return ppr
    base = row.get("FantasyPoints")
    if isinstance(base, (int, float)):
        return base
    # sometimes numbers come in as strings
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
@app.route("/api/rankings", methods=["GET"])
def rankings():
    # Season format: your frontend has used "2025REG". Keep that as default.
    season = request.args.get("season", "2025REG")

    try:
        baker_data = _get_cached(
            f"baker:{season}", lambda: _fetch_baker(season)
        )
        adp_data = _get_cached(
            f"adp:{season}", lambda: _fetch_adp_and_points(season)
        )
    except Exception as e:
        print("Error fetching data:", e)
        return jsonify([]), 500

    # Build ADP/points lookups
    # Primary key: PlayerID; Secondary: (Name, Team)
    adp_by_id = {}
    adp_by_name_team = {}

    for r in adp_data or []:
        pid = str(r.get("PlayerID")) if r.get("PlayerID") is not None else None
        adp_val = _pick_adp_from_adp_row(r)
        pts_val = _pick_points_from_adp_row(r)

        if pid:
            adp_by_id[pid] = {"adp": adp_val, "points": pts_val}

        key = (_norm(r.get("Name")), _norm(r.get("Team")))
        adp_by_name_team[key] = {"adp": adp_val, "points": pts_val}

    # Merge into Baker rows; prefer PlayerID match, else name+team
    merged = []
    for p in baker_data or []:
        pid = str(p.get("player_id") or p.get("PlayerID") or "").strip() or None
        name = p.get("name") or p.get("Name")
        team = p.get("team") or p.get("Team")
        pos = p.get("position") or p.get("Position")

        adp_pts = None
        if pid and pid in adp_by_id:
            adp_pts = adp_by_id[pid]
        else:
            adp_pts = adp_by_name_team.get((_norm(name), _norm(team)))

        merged.append({
            "playerId": pid or p.get("PlayerID"),
            "name": name,
            "team": team,
            "position": pos,
            "adp": (adp_pts or {}).get("adp"),
            # IMPORTANT: points now from FantasyPointsPPR (same ADP endpoint)
            "points": (adp_pts or {}).get("points"),
        })

    # If ADP payload contains players not in Baker, include them too
    # (useful if Baker misses kickers/IDP edge cases)
    seen = set((m["playerId"], _norm(m["name"]), _norm(m["team"])) for m in merged)
    for r in adp_data or []:
        pid = str(r.get("PlayerID")) if r.get("PlayerID") is not None else None
        nm = r.get("Name")
        tm = r.get("Team")
        pos = r.get("Position")
        key = (pid, _norm(nm), _norm(tm))
        if key in seen:
            continue
        merged.append({
            "playerId": pid,
            "name": nm,
            "team": tm,
            "position": pos,
            "adp": _pick_adp_from_adp_row(r),
            "points": _pick_points_from_adp_row(r),
        })

    # Sort by ADP ascending (None at bottom)
    merged_sorted = sorted(
        merged,
        key=lambda x: (x.get("adp") is None, x.get("adp") if x.get("adp") is not None else float("inf"))
    )

    # Add rank
    ranked = []
    for i, p in enumerate(merged_sorted, start=1):
        ranked.append({
            "rank": i,
            "playerId": p.get("playerId"),
            "name": p.get("name"),
            "team": p.get("team"),
            "position": p.get("position"),
            "adp": p.get("adp"),
            "points": p.get("points"),  # <-- PPR points
        })

    return jsonify(ranked)


# Legacy alias your frontend calls
@app.route("/api/get-ranked-players", methods=["GET"])
def get_ranked_players_alias():
    return rankings()


if __name__ == "__main__":
    # For local dev
    app.run(host="0.0.0.0", port=5001, debug=True)
