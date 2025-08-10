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

BAKER_KEY = os.getenv("SPORTSDATA_BAKER_KEY")  # set this in your env
BAKER_BASE = "https://baker-api.sportsdata.io/baker/v2/nfl/projections/players/full-season"

# simple in-memory cache { cache_key: (timestamp, data) }
_CACHE = {}
TTL_SECONDS = 600  # 10 minutes

def _cache_get(key):
    ts_data = _CACHE.get(key)
    if not ts_data:
        return None
    ts, data = ts_data
    if time.time() - ts > TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return data

def _cache_set(key, data):
    _CACHE[key] = (time.time(), data)

def _fetch_baker(season_name: str):
    if not BAKER_KEY:
        raise RuntimeError("SPORTSDATA_BAKER_KEY not set")
    url = f"{BAKER_BASE}/{season_name}/avg"
    resp = requests.get(url, params={"key": BAKER_KEY}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def _pick_points(row, scoring: str):
    # Try best-guess field names with graceful fallbacks
    if scoring == "ppr":
        return row.get("FantasyPointsPPR") or row.get("FantasyPoints") or 0
    if scoring in ("half_ppr", "half-ppr", "half"):
        return (row.get("FantasyPointsHalfPPR")
                or row.get("FantasyPointsPPR")  # fallback
                or row.get("FantasyPoints")    # fallback
                or 0)
    # standard / non-PPR
    return row.get("FantasyPoints") or row.get("FantasyPointsStandard") or 0

def _minify(row, rank, points):
    # Return only what your app needs; add/remove fields as you like
    return {
        "rank": rank,
        "playerId": row.get("PlayerID") or row.get("playerId"),
        "name": row.get("Name") or row.get("name"),
        "team": row.get("Team") or row.get("team"),
        "position": row.get("Position") or row.get("position"),
        "points": points
    }

@app.route("/api/rankings", methods=["GET"])
def rankings():
    season = request.args.get("season", "2025REG")  # Baker season_name
    # We'll sort on half_ppr always unless you want to make it dynamic
    scoring_field = "fantasy_points_half_ppr"

    try:
        data = _fetch_baker(season)  # direct from Baker API
    except Exception as e:
        print("Error fetching Baker data:", e)
        return jsonify([]), 500

    # Defensive: ensure it's a list and sort by the desired field
    if not isinstance(data, list):
        return jsonify([]), 500

    sorted_players = sorted(
        data,
        key=lambda p: float(p.get(scoring_field, 0) or 0),
        reverse=True
    )

    ranked = []
    for i, p in enumerate(sorted_players, start=1):
        ranked.append({
            "rank": i,
            "playerId": p.get("player_id") or p.get("PlayerID"),
            "name": p.get("name") or p.get("Name"),
            "team": p.get("team") or p.get("Team"),
            "position": p.get("position") or p.get("Position"),
            "points": p.get(scoring_field)
        })

    return jsonify(ranked)



# Optional: keep this legacy route name if your frontend already calls it
@app.route("/api/get-ranked-players", methods=["GET"])
def get_ranked_players_alias():
    return rankings()

if __name__ == "__main__":
    # For local dev
    app.run(host="0.0.0.0", port=5001, debug=True)
