# api.py
from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import requests
from dotenv import load_dotenv
load_dotenv()


app = Flask(__name__)
CORS(app)

BAKER_KEY = os.getenv("SPORTSDATA_BAKER_KEY")  # set this in your env
BAKER_BASE = "https://baker-api.sportsdata.io/baker/v2/nfl/projections/players/full-season"

# simple in-memory cache { cache_key: (timestamp, data) }
TTL_SECONDS = 600  # 10 minutes


def _fetch_baker(season):
    url = f"https://baker-api.sportsdata.io/baker/v2/nfl/projections/players/full-season/{season}/avg"
    resp = requests.get(url, params={"key": BAKER_KEY})
    resp.raise_for_status()
    return resp.json()

def _fetch_adp(season):
    url = f"https://api.sportsdata.io/v3/nfl/projections/json/PlayerSeasonProjectionStats/{season}"
    resp = requests.get(url, params={"key": BAKER_KEY})
    resp.raise_for_status()
    return resp.json()

@app.route("/api/rankings", methods=["GET"])
def rankings():
    season = request.args.get("season", "2025REG")
    scoring_field = "fantasy_points_half_ppr"

    try:
        # Fetch both datasets
        baker_data = _fetch_baker(season)
        adp_data = _fetch_adp(season)
    except Exception as e:
        print("Error fetching data:", e)
        return jsonify([]), 500

    # Map ADP by PlayerID
    adp_map = {
        str(player["PlayerID"]): player.get("AverageDraftPositionPPR")
        for player in adp_data
        if "PlayerID" in player
    }

    # Merge ADP into Baker data
    for player in baker_data:
        pid = str(player.get("player_id") or player.get("PlayerID"))
        player["ADP"] = adp_map.get(pid)

    # Sort by ADP ascending (None values go to bottom)
    sorted_players = sorted(
        baker_data,
        key=lambda p: (p.get("ADP") is None, p.get("ADP", float("inf")))
    )

    # Build ranked list
    ranked = []
    for i, p in enumerate(sorted_players, start=1):
        ranked.append({
            "rank": i,
            "playerId": p.get("player_id") or p.get("PlayerID"),
            "name": p.get("name") or p.get("Name"),
            "team": p.get("team") or p.get("Team"),
            "position": p.get("position") or p.get("Position"),
            "adp": p.get("ADP"),
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
