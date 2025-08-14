from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import time
import requests
import json
import re
from dotenv import load_dotenv
from openai import OpenAI
import traceback

# ---------------------------
# Init
# ---------------------------
load_dotenv()

app = Flask(__name__)
CORS(app)

SPORTSDATA_KEY = os.getenv("SPORTSDATA_BAKER_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
TIMEOUT = 20
TTL_SECONDS = 600  # 10 minutes cache

client = OpenAI(api_key=OPENAI_KEY)

TENDENCIES_PATH = os.path.join(os.path.dirname(__file__), "data", "league_tendencies.json")

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
    return (s or "").strip().lower()

def _norm_team(team):
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
    }
    t = team.strip().lower()
    return team_map.get(t, t)

def _is_valid_headshot(url):
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
# Tendencies helpers
# ---------------------------
def load_tendencies():
    if not os.path.exists(TENDENCIES_PATH):
        return {"teams": {}, "scoring": {"half_ppr": True}, "global_habits": []}
    with open(TENDENCIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_tendencies(obj):
    os.makedirs(os.path.dirname(TENDENCIES_PATH), exist_ok=True)
    with open(TENDENCIES_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

def summarize_my_roster(roster):
    slots = [f"{k}:{v['name']}({v['position']})" for k, v in roster.items() if v]
    return ", ".join(slots) or "Empty"

def build_prompt(player, my_team_name, my_roster, tendencies, meta):
    return {
        "league": tendencies,
        "meta": meta,
        "me": {
            "team": my_team_name,
            "roster_summary": summarize_my_roster(my_roster),
        },
        "candidate": player
    }

# ---------------------------
# Fit score normalizer (always return 0–100 int)
# ---------------------------
_num_re = re.compile(r"[-+]?\d*\.?\d+")
def _normalize_fit_score(raw):
    if raw is None:
        return 75
    # strings like "9/10", "92%", "Fit: 8.5", etc.
    if isinstance(raw, str):
        s = raw.strip()
        # 9/10 → 90
        if "/" in s:
            parts = s.split("/", 1)
            try:
                num = float(_num_re.search(parts[0]).group())
                den = float(_num_re.search(parts[1]).group())
                if den > 0:
                    return int(max(0, min(100, round(100 * num / den))))
            except Exception:
                pass
        # fallthrough to first number in string
        m = _num_re.search(s)
        if m:
            try:
                raw = float(m.group())
            except Exception:
                pass

    # numbers
    try:
        v = float(raw)
    except Exception:
        return 75

    # If model returned 0–1, scale to 0–100
    if 0 <= v <= 1:
        v = v * 100
    # If model returned 0–10, scale to 0–100
    elif 0 <= v <= 10:
        v = v * 10
    # else assume already 0–100

    return int(max(0, min(100, round(v))))

# ---------------------------
# Routes - Tendencies + AI
# ---------------------------
@app.route("/api/league-tendencies", methods=["GET"])
def get_tendencies():
    return jsonify(load_tendencies())

@app.route("/api/league-tendencies", methods=["POST"])
def upsert_tendencies():
    payload = request.get_json(force=True)
    cur = load_tendencies()
    for k, v in payload.items():
        cur[k] = v
    save_tendencies(cur)
    return jsonify({"ok": True, "tendencies": cur})

@app.route("/api/ai/opinion", methods=["POST"])
def ai_opinion():
    try:
        body = request.get_json(force=True)
        tendencies = load_tendencies()
        ctx = build_prompt(
            player=body["player"],
            my_team_name=body["myTeamName"],
            my_roster=body.get("myRoster", {}),
            tendencies=tendencies,
            meta=body.get("meta", {})
        )

        system_msg = (
            "You are DraftIQ Coach. Give precise, league-aware fantasy advice for a 1/2 PPR draft. "
            "Use the provided data only. If you reference tendencies, tie them to specific teams. "
            "Weigh projection, VORP, positional cliffs, ADP value, bye conflicts, and roster construction. "
            "Respond ONLY in valid JSON matching the schema: "
            '{"verdict": "DRAFT|PASS", "fitScore": number, "rationale": string, '
            '"pros": [string], "cons": [string], "risks": [string], '
            '"suggestedAlternatives": [{"name": string, "position": string, "reason": string}] }'
        )

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_msg},
                {
                    "role": "user",
                    "content": f"Context follows in JSON: {json.dumps(ctx)}"
                }
            ],
            temperature=0.7,
            max_tokens=500
        )

        raw_content = resp.choices[0].message.content.strip()

        # Remove triple backticks if present
        if raw_content.startswith("```"):
            raw_content = raw_content.strip("`")
            if raw_content.lower().startswith("json"):
                raw_content = raw_content[4:].strip()

        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            print("Model returned invalid JSON, returning raw output")
            return jsonify({"error": "Invalid JSON from model", "raw": raw_content}), 500

        # Map possible alternate keys to expected schema
        opinion = {
            "verdict": parsed.get("verdict") or parsed.get("decision") or "PASS",
            "fitScore": _normalize_fit_score(parsed.get("fitScore")),
            "rationale": parsed.get("rationale") or " ".join(parsed.get("reasons", [])),
            "pros": parsed.get("pros", []),
            "cons": parsed.get("cons", []),
            "risks": parsed.get("risks", []),
            "suggestedAlternatives": parsed.get("suggestedAlternatives", [])
        }

        return jsonify({"opinion": opinion, "usedTendencies": tendencies})

    except Exception as e:
        print("Error in /api/ai/opinion:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ---------------------------
# Routes - Existing rankings
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

    merged = []
    for p in baker_data or []:
        pid = str(p.get("player_id") or p.get("PlayerID") or "").strip() or None
        name = p.get("name") or p.get("Name")
        team = p.get("team") or p.get("Team")
        pos = p.get("position") or p.get("Position")

        name_team_key = (_norm(name), _norm_team(team))
        adp_pts = adp_by_id.get(pid) if pid in adp_by_id else adp_by_name_team.get(name_team_key)

        headshot_url = None
        if pid and pid in headshot_by_id and _is_valid_headshot(headshot_by_id[pid]):
            headshot_url = headshot_by_id[pid]
        if not _is_valid_headshot(headshot_url) and name_team_key in headshot_by_name_team:
            if _is_valid_headshot(headshot_by_name_team[name_team_key]):
                headshot_url = headshot_by_name_team[name_team_key]

        merged.append({
            "playerId": pid or p.get("PlayerID"),
            "name": name,
            "team": team,
            "position": pos,
            "adp": (adp_pts or {}).get("adp"),
            "points": (adp_pts or {}).get("points"),
            "headshot": headshot_url if _is_valid_headshot(headshot_url) else None
        })

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

        merged.append({
            "playerId": pid,
            "name": nm,
            "team": tm,
            "position": pos,
            "adp": _pick_adp_from_adp_row(r),
            "points": _pick_points_from_adp_row(r),
            "headshot": headshot_url if _is_valid_headshot(headshot_url) else None
        })

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

# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
