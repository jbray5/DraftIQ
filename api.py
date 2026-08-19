from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import time
import requests
import json
import re
import csv
from dotenv import load_dotenv
import anthropic
import traceback
import headshots as headshots_mod

# ---------------------------
# Init
# ---------------------------
load_dotenv()

app = Flask(__name__)
CORS(app)

SPORTSDATA_KEY = os.getenv("SPORTSDATA_BAKER_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
# Coach model — override via .env. Default to the most capable Claude; drop to
# claude-sonnet-4-6 / claude-haiku-4-5 for cheaper, snappier live drafting.
COACH_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
# Reasoning depth vs latency for the live coach: low | medium | high.
COACH_EFFORT = os.getenv("ANTHROPIC_EFFORT", "medium")
TIMEOUT = 20
TTL_SECONDS = 600  # 10 minutes cache

# Only construct the client if a key is present, so the rankings endpoints still
# work without an Anthropic key configured.
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

TENDENCIES_PATH = os.path.join(os.path.dirname(__file__), "data", "league_tendencies.json")
BOARD_PATH = os.path.join(os.path.dirname(__file__), "data", "processed", "board_2026.csv")
PROFILES_PATH = os.path.join(os.path.dirname(__file__), "data", "processed", "manager_profiles.json")


def load_manager_profiles(active_only=True):
    """The DraftIQ-built opponent tendency sheet (see models/opponent_profiles.py)."""
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            profs = json.load(f)
        vals = list(profs.values())
        return [p for p in vals if p.get("active_2026")] if active_only else vals
    except Exception:
        return []


def load_profiles_by_owner(active_only=True):
    """Owner-keyed tendency profiles (the pick engine looks managers up by owner handle)."""
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            profs = json.load(f)
        return {k: v for k, v in profs.items() if v.get("active_2026")} if active_only else profs
    except Exception:
        return {}


# Shared, VALIDATED league facts injected into every coach prompt. ONE copy — keep it
# current (it previously drifted: three endpoints carried a stale "IDP fully streamable"
# claim from before the market-timing model and the 2026-08-11 tackle ruling).
LEAGUE_INSIGHTS = (
    "VALIDATED facts from 14 seasons of THIS league's history -> results: "
    "(1) Value over replacement (VORP) is what wins here (corr +0.56 with points-for); raw "
    "projected points is a much weaker signal and rigid RB-early/WR-early templates do NOT "
    "predict winning. (2) QB can WAIT — champions took their first QB ~round 6 in EVERY era; "
    "QB replacement is flat, so the cost of waiting a round is usually 1-3 season points. "
    "(3) D/ST and K are pure STREAMING plays: D/ST identity is nearly irrelevant (week-to-week "
    "persistence +0.001) and the board orders D/ST by WEEK-1 matchup (implied opponent total, "
    "r=-0.294, worth ~+55 pts/season streamed); take one D/ST late by matchup and a K in the "
    "final round. (4) IDP is MARKET-TIMED, NOT streamed: an elite IDP is held all year "
    "(weekly persistence ~= TE), but the room never pays early — first IDP off the board "
    "~round 9 historically, 0 of 14 champions took an IDP by round 8, and the elite tier is "
    "~5 deep. The play: wait until ~pick 90-95, then take the top LB (top IDP ranks ~#41 "
    "overall). (5) RB-leaning room; with the 2nd FLEX (new 2026) both RB and WR depth matter. "
    "TE almost never flexes here (1.4% of 1,151 real FLEX starts) — never take a 2nd TE for a "
    "FLEX slot; the engine caps flex-only TE value at the best available RB/WR for this reason. "
    "(6) EARLY-round VORP (rds 1-5) carries the draft signal (corr -0.43 w/ finish); champions "
    "drafted at the 92nd pctile of value THAT year, but draft outcomes do NOT repeat "
    "year-to-year — the repeatable owner skills are in-season streaming and waivers. "
    "(7) Scoring ruling 2026-08-11 (commish-confirmed): tackles score 1.0 FLAT (the solo/asst "
    "double-pay was a rollover misconfiguration, now removed), PD 1.5, sacks 2.0 — the board "
    "is already priced under these rules (top IDP ~200 pts, ~12/wk)."
)

_COACH_PERSONA = (
    "You are DraftIQ Coach, a sharp, league-aware fantasy football draft advisor for a "
    "10-team, half-PPR ESPN league ('Derek Jeter's Taco Hole') that starts QB, RB, RB, "
    "WR, WR, TE, FLEX, FLEX, IDP (the DP slot), D/ST, and K, plus bench. Reason from "
    "value-over-replacement for THIS roster, positional scarcity/runs, ADP value, "
    "pick-timing (who's likely gone before the user's next pick), bye conflicts, and "
    "roster construction. The candidate may include `vorp` and `draftScore` (the DraftIQ "
    "model's value) — trust them. When you cite an opponent tendency, name the team and "
    "WEIGHT IT BY CONFIDENCE (ignore 'low'-confidence / new-manager reads). Use only the "
    "data provided. Be decisive and concise — the user is on the clock.\n\n"
    "PICK TYPES — classify every candidate you discuss: SAFE FLOOR (projection-backed "
    "starter), CEILING DART (negative-VORP bench swing where the market/experts see a role "
    "our projection doesn't), HANDCUFF (value gated on an injury ahead of him), or STREAMER "
    "(K/D-ST/late IDP). For CEILING DARTS always give a PRICE-CONDITIONED verdict — name "
    "the pick where he becomes worth it, anchored to his market cost (fp_adp/ecr), e.g. "
    "'worth a swing at 130+, a reach at 100'. Dart upside signals, in order: market premium "
    "(fp_adp/ecr far earlier than our projection rank = someone sees a role), fp_up/fp_bust "
    "(FantasyPros' coach Upside/Bust ratings, 1-5 — 4+ is their 'High' tag), and when "
    "present fp_best (the most bullish expert's rank = perceived CEILING) vs fp_std (expert "
    "disagreement = BUST risk). Backtested caveat: projection order is still the best "
    "primary dart ranker (2023-24: 35-50% hit vs 25% for pure market premium) — treat "
    "upside signals as tiebreakers among comparable projections, never as the ranking.\n\n"
)


def coach_system_blocks():
    """The shared coach system prompt: persona + validated insights, then the cached
    league-config block. Byte-identical across /api/ai/opinion, /best-pick, and /chat so
    all three read the same prompt-cache entry."""
    return [
        {"type": "text", "text": _COACH_PERSONA + LEAGUE_INSIGHTS},
        {
            "type": "text",
            "text": ("League scoring + tendencies:\n" + json.dumps(load_tendencies())
                     + "\n\nActive 2026 opponents (tendency sheet — respect each one's confidence):\n"
                     + json.dumps(load_manager_profiles(active_only=True))),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def load_alias_map():
    """data/team_aliases.json — live UI short name -> ESPN owner handle (manager-prior bridge)."""
    p = os.path.join(os.path.dirname(__file__), "data", "team_aliases.json")
    try:
        with open(p, encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except Exception:
        return {}

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

def _fetch_byes(season: str):
    url = f"https://api.sportsdata.io/v3/nfl/scores/json/Byes/{season}"
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
    # SportsData returns the literal string "Scrambled" for fields outside the
    # plan — require a real URL so those never reach the UI as an <img src>.
    if not lowered.startswith(("http://", "https://")):
        return False
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

        if client is None:
            return jsonify({"error": "ANTHROPIC_API_KEY not set on the server"}), 503

        # Scoring + tendencies live in the cached system prompt below, so drop the
        # duplicate from the per-pick context and add a compact opponent board so
        # the coach can see positional runs / who's already gone.
        ctx.pop("league", None)
        board = body.get("board", {})
        if isinstance(board, dict):
            ctx["board"] = {
                team: [v["name"] for v in slots.values() if v]
                for team, slots in board.items()
            }

        # Stable across picks -> cached. Shared persona + insights + league config.
        system_blocks = coach_system_blocks()

        opinion_format = {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["DRAFT", "PASS"]},
                    "fitScore": {"type": "integer"},
                    "rationale": {"type": "string"},
                    "pros": {"type": "array", "items": {"type": "string"}},
                    "cons": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "suggestedAlternatives": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "position": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["name", "position", "reason"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "verdict", "fitScore", "rationale",
                    "pros", "cons", "risks", "suggestedAlternatives",
                ],
                "additionalProperties": False,
            },
        }

        output_config = {"format": opinion_format}
        if COACH_EFFORT:  # set ANTHROPIC_EFFORT="" to drop the effort knob entirely
            output_config["effort"] = COACH_EFFORT

        resp = client.messages.create(
            model=COACH_MODEL,
            max_tokens=1024,
            system=system_blocks,
            output_config=output_config,
            messages=[
                {
                    "role": "user",
                    "content": "Evaluate this candidate for my next pick. Context JSON:\n"
                    + json.dumps(ctx),
                }
            ],
        )

        if resp.stop_reason == "refusal":
            return jsonify({"error": "Model declined to answer"}), 502

        # output_config.format guarantees the first text block is valid JSON.
        raw_content = next(b.text for b in resp.content if b.type == "text")
        parsed = json.loads(raw_content)

        fit = _normalize_fit_score(parsed.get("fitScore"))

        # The model's self-reported fit/verdict are unreliable for streamers/redundancy
        # (it will write "never roster two kickers" yet return fit 80). Derive the
        # verdict DETERMINISTICALLY from hard signals so the tag can't contradict
        # reality; the LLM supplies only the rationale/pros/cons.
        def _posb(p):
            p = str(p or "").upper().replace(".", "").replace("/", "").replace(" ", "")
            if p in ("K", "PK"): return "K"
            if p in ("DST", "DEF", "D"): return "DST"
            if p in ("QB", "RB", "WR", "TE"): return p
            return "IDP"
        cand = body.get("player", {}) or {}
        cpos = _posb(cand.get("position"))
        roster = body.get("myRoster", {}) or {}
        filled = {}
        if isinstance(roster, dict):
            for v in roster.values():
                if isinstance(v, dict) and v.get("position"):
                    b = _posb(v["position"]); filled[b] = filled.get(b, 0) + 1
        meta = body.get("meta", {}) or {}
        try:
            pick_no, teams = int(meta.get("pickNumber") or 0), int(meta.get("teams") or 10)
        except Exception:
            pick_no, teams = 0, 10
        rnd = (pick_no + teams - 1) // teams if (pick_no and teams) else 0
        STREAM = {"K", "DST", "IDP"}

        if cpos in STREAM and filled.get(cpos, 0) >= 1:
            verdict = "PASS"                            # redundant streamer — never roster two
        elif cpos in STREAM:
            verdict = "DRAFT" if rnd >= 15 else "PASS"  # stream K/DST/IDP — only grab your one very late
        else:
            verdict = "DRAFT" if fit >= 50 else "PASS"
        fit = min(fit, 40) if verdict == "PASS" else max(fit, 58)  # keep displayed fit consistent
        opinion = {
            "verdict": verdict,
            "fitScore": fit,
            "rationale": parsed.get("rationale", ""),
            "pros": parsed.get("pros", []),
            "cons": parsed.get("cons", []),
            "risks": parsed.get("risks", []),
            "suggestedAlternatives": parsed.get("suggestedAlternatives", []),
        }

        return jsonify({"opinion": opinion, "usedTendencies": tendencies})

    except Exception as e:
        print("Error in /api/ai/opinion:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ---------------------------
# Routes - Existing rankings
# ---------------------------
@app.route("/api/ai/closing", methods=["POST"])
def ai_closing():
    """Post-draft strategic brief: how to win the league from here (QB plan,
    trade-from-strength angles by opponent tendency, waiver/FAAB strategy)."""
    if client is None:
        return jsonify({"error": "ANTHROPIC_API_KEY not set on the server"}), 503
    try:
        body = request.get_json(force=True)
        roster = body.get("myRoster", [])
        tendencies = load_tendencies()
        profiles = load_manager_profiles(active_only=True)
        insights = (
            "VALIDATED facts from 14 seasons of THIS league: VORP wins (corr +0.56); QB can "
            "wait; K/D-ST stream weekly (D/ST by matchup); IDP is held all year but bought at "
            "the ~rd-9 market window; RB-leaning room. Rival to beat: tcspivey8 (4 titles)."
        )
        system_blocks = [
            {
                "type": "text",
                "text": (
                    "You are DraftIQ Coach delivering a concise POST-DRAFT closing brief for a "
                    "10-team, half-PPR ESPN league ('Derek Jeter's Taco Hole') that starts QB, RB, "
                    "RB, WR, WR, TE, FLEX, FLEX, IDP (DP), D/ST, K. Given the user's drafted roster, "
                    "lay out how to WIN THE LEAGUE FROM HERE: the QB plan (stream vs trade), "
                    "trade-from-strength angles (name a likely partner using their tendencies and "
                    "needs), and waiver/FAAB strategy. Cite the user's ACTUAL players, weight opponent "
                    "reads by confidence (ignore low-confidence/new managers), be specific and "
                    "decisive.\n\n" + insights
                ),
            },
            {
                "type": "text",
                "text": ("League scoring:\n" + json.dumps(tendencies)
                         + "\n\nActive 2026 opponents (respect confidence):\n" + json.dumps(profiles)),
                "cache_control": {"type": "ephemeral"},
            },
        ]
        closing_format = {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["grade", "headline", "moves", "signoff"],
                "properties": {
                    "grade": {"type": "string"},
                    "headline": {"type": "string"},
                    "moves": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["title", "detail"],
                            "properties": {"title": {"type": "string"}, "detail": {"type": "string"}},
                        },
                    },
                    "signoff": {"type": "string"},
                },
            },
        }
        output_config = {"format": closing_format}
        if COACH_EFFORT:
            output_config["effort"] = COACH_EFFORT
        resp = client.messages.create(
            model=COACH_MODEL, max_tokens=1500, system=system_blocks,
            output_config=output_config,
            messages=[{"role": "user",
                       "content": "My drafted roster (JSON). Give my closing plan to win the league:\n"
                       + json.dumps(roster)}],
        )
        if resp.stop_reason == "refusal":
            return jsonify({"error": "Model declined to answer"}), 502
        parsed = json.loads(next(b.text for b in resp.content if b.type == "text"))
        return jsonify({"closing": parsed})
    except Exception as e:
        print("Error in /api/ai/closing:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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

    # Optional cap on results. Default returns everything (drafts need deep IDP/K/DST).
    raw_limit = request.args.get("limit")
    if raw_limit is None or raw_limit.lower() == "all":
        limit = None
    else:
        try:
            limit = max(1, min(int(raw_limit), 2000))
        except Exception:
            limit = None

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

    espn_heads = headshots_mod.get_map()   # primary headshot source (see headshots.py)

    merged = []
    for p in baker_data or []:
        pid = str(p.get("player_id") or p.get("PlayerID") or "").strip() or None
        name = p.get("name") or p.get("Name")
        team = p.get("team") or p.get("Team")
        pos = p.get("position") or p.get("Position")

        name_team_key = (_norm(name), _norm_team(team))
        adp_pts = adp_by_id.get(pid) if pid in adp_by_id else adp_by_name_team.get(name_team_key)

        headshot_url = headshots_mod.resolve(name, team, pos, players=espn_heads)
        if not headshot_url and pid and pid in headshot_by_id and _is_valid_headshot(headshot_by_id[pid]):
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
        headshot_url = headshots_mod.resolve(nm, tm, pos, players=espn_heads)
        if not headshot_url and pid and pid in headshot_by_id and _is_valid_headshot(headshot_by_id[pid]):
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

    if limit is not None:
        merged_sorted = merged_sorted[:limit]

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


@app.route("/api/board", methods=["GET"])
def board():
    """The calibrated DraftIQ board (models/build_board.py -> board_2026.csv): every
    player scored in league rules with VORP + DraftIQ value, plus headshots merged in.
    Shaped for the frontend's normalizePlayer (playerId/name/team/position/points/adp)."""
    if not os.path.exists(BOARD_PATH):
        return jsonify([]), 404
    season = request.args.get("season", "2026REG")
    with open(BOARD_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # bye weeks by NFL team
    try:
        byes = _get_cached(f"byes:{season}", lambda: _fetch_byes(season))
    except Exception:
        byes = []
    bye_by_team = {str(b.get("Team", "")).upper(): b.get("Week") for b in (byes or [])}

    # Headshots: ESPN's public roster feed is the primary source (free, freshest,
    # ~99% board coverage). SportsData's feed is kept only as a fallback for the
    # day its Headshots endpoint is actually in the plan — today it returns
    # "Scrambled" for every URL, which _is_valid_headshot now rejects.
    espn_heads = headshots_mod.get_map()
    try:
        headshots = _get_cached("headshots", _fetch_headshots)
    except Exception:
        headshots = []
    hs_by_id = {}
    for h in headshots or []:
        pid = str(h.get("PlayerID")) if h.get("PlayerID") is not None else None
        url = (h.get("PreferredHostedHeadshotUrl") or h.get("HostedHeadshotNoBackgroundUrl")
               or h.get("HostedHeadshotWithBackgroundUrl"))
        if pid and _is_valid_headshot(url):
            hs_by_id[pid] = url

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # WEEK 1 MATCHUP for the streaming slots. The D/ST you draft in round 16 is a
    # week-1 starter, and the matchup (implied opponent total) predicts its score
    # far better than the unit's identity does. Kickers get the mirror number for
    # tie-breaking only — their signal is weak (see models/week1_odds.py).
    w1_dst, w1_k = {}, {}
    try:
        from models import week1_odds
        w1_dst = week1_odds.dst_ranks(2026, 1)
        w1_k = week1_odds.k_context(2026, 1)
    except Exception as e:
        print(f"/api/board: week-1 odds unavailable ({e})")

    out = []
    for r in rows:
        pid = str(r.get("playerId") or "").split(".")[0] or None
        head = headshots_mod.resolve(r.get("name"), r.get("team"), r.get("pos"),
                                     players=espn_heads) or hs_by_id.get(pid)
        pos = r.get("pos")
        w1 = (w1_dst if pos == "DST" else w1_k if pos == "K" else {}).get(
            str(r.get("team", "")).upper())
        out.append({
            "rank": int(num(r.get("rank"))) if num(r.get("rank")) is not None else None,
            "playerId": pid,
            "name": r.get("name"),
            "team": r.get("team"),
            "position": r.get("pos"),
            "points": num(r.get("league_pts")),
            "vorp": num(r.get("vorp")),
            "draftScore": num(r.get("draft_value")),
            "adp": num(r.get("adp")),
            "bye": bye_by_team.get(str(r.get("team", "")).upper()),
            "headshot": head,
            "ecr": int(num(r.get("ecr"))) if num(r.get("ecr")) is not None else None,
            "ecrTier": int(num(r.get("ecr_tier"))) if num(r.get("ecr_tier")) is not None else None,
            "sos": int(num(r.get("sos"))) if num(r.get("sos")) is not None else None,
            "fpAdp": num(r.get("fp_adp")),
            # expert spread (from the FP consensus export when present): best = most
            # bullish expert's rank (ceiling read), std = disagreement (bust risk)
            "fpBest": num(r.get("fp_best")),
            "fpWorst": num(r.get("fp_worst")),
            "fpStd": num(r.get("fp_std")),
            # FP coach ratings (1-5, real values since the 8/13 export)
            "fpUp": int(num(r.get("fp_up"))) if num(r.get("fp_up")) is not None else None,
            "fpBust": int(num(r.get("fp_bust"))) if num(r.get("fp_bust")) is not None else None,
            # weeks played in 2025 (durability flag; None = no league box-score data)
            "wkPrev": num(r.get("wk25")),
            # week-1 matchup (D/ST + K only; None everywhere else)
            "w1Rank": w1.get("w1Rank") if w1 else None,
            "w1Tier": w1.get("w1Tier") if w1 else None,
            "w1Opp": w1.get("opp") if w1 else None,
            "w1Home": w1.get("isHome") if w1 else None,
            "w1Spread": w1.get("spread") if w1 else None,
            "w1OppTotal": w1.get("impliedOppTotal") if w1 else None,
            "w1OwnTotal": w1.get("impliedTeamTotal") if w1 else None,
        })
    return jsonify(out)


@app.route("/api/trade", methods=["POST"])
def api_trade():
    """Evaluate a proposed trade vs the user's roster (models/inseason.trade_eval):
    ROS starting-lineup point delta + give/get value + ACCEPT/DECLINE/COUNTER."""
    try:
        from models import inseason
        body = request.get_json(force=True)
        my = body.get("myRoster", [])      # [{name,pos,proj}]
        give = body.get("give", [])         # [name]
        get = body.get("get", [])           # [{name,pos,proj}]
        spec = inseason.slot_spec(inseason.CFG["2026"])
        return jsonify(inseason.trade_eval(my, give, get, spec))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _load_board_rows():
    """The full DraftIQ board (data/processed/board_2026.csv) as a list of dicts."""
    rows = []
    if os.path.exists(BOARD_PATH):
        with open(BOARD_PATH, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    return rows


@app.route("/api/ai/best-pick", methods=["POST"])
def ai_best_pick():
    """The fused 'Optimal Pick Right Now'. A deterministic need/survival/run-aware shortlist
    (models.pick_engine, re-scored at the LIVE pick) is handed to Claude, which names THE
    pick + 2-3 NAMED alternatives + urgency, choosing ONLY from the available shortlist. The
    deterministic layer owns legality/urgency; the LLM owns which legal player + the prose."""
    try:
        from models import pick_engine

        body = request.get_json(force=True)
        my_roster = body.get("myRoster", {}) or {}
        drafted = body.get("drafted", []) or []
        picks = body.get("picks", []) or []
        meta = body.get("meta", {}) or {}
        overall_pick = int(meta.get("pickNumber") or 1)
        teams = int(meta.get("teams") or 10)
        rounds = int(meta.get("rounds") or 16)
        my_slot = body.get("mySlot")
        bench = body.get("bench") or []        # your bench players (for positional saturation)
        team_order = body.get("teamOrder") or []
        team_rosters = body.get("teamRosters")
        if not team_rosters and picks:                      # derive per-team rosters from the pick log
            team_rosters = {}
            for pk in picks:
                t = pk.get("team")
                if t:
                    team_rosters.setdefault(t, []).append(
                        {"name": pk.get("name"), "position": pk.get("pos")})

        rows = _load_board_rows()
        if not rows:
            return jsonify({"error": "board not built — run python models/build_board.py"}), 503
        sl = pick_engine.shortlist(rows, set(drafted), my_roster, picks, overall_pick,
                                   teams=teams, team_order=team_order,
                                   team_rosters=team_rosters or {}, my_slot=my_slot, rounds=rounds,
                                   bench=bench, profiles=load_profiles_by_owner(active_only=True),
                                   alias_map=load_alias_map())
        shortlist, anchor = sl["shortlist"], sl["anchor"]
        if not shortlist:
            return jsonify({"error": "no available players in contention"}), 200
        names = {c["name"] for c in shortlist}
        by_name = {c["name"]: c for c in shortlist}
        umeta = {"nextPick": sl["meta"]["nextPick"], "picksUntilNext": sl["meta"]["picksUntilNext"],
                 "nextTurnPick": sl["meta"].get("nextTurnPick"),
                 "backToBack": sl["meta"].get("backToBack"),
                 "flat": sl["flat"], "round": sl["meta"]["round"]}

        def _card(name, why):
            c = by_name.get(name)
            if not c:
                return None
            return {"name": c["name"], "position": c["position"], "team": c.get("team"),
                    "adp": c.get("adp"), "tier": c.get("tier"), "vorp": c.get("vorp"),
                    "proj": c.get("proj"), "fills": c.get("fits"), "confidence": c.get("confidence"),
                    "urgency": c["urgency"], "oneLineWhy": why or c.get("why")}

        # ---- no-LLM fallback: deterministic anchor + top alternatives (still fully usable) ----
        if client is None:
            wait = next((c for c in shortlist if c["urgency"] == "can-wait"), None)
            return jsonify({
                "optimalPick": _card(anchor, None),
                "alternatives": [_card(c["name"], None) for c in shortlist[1:3]],
                "waitSignal": (f"You can wait on {wait['name']} — ADP {wait['adp']} is past your next pick."
                               if wait else ""),
                "synthesis": ("Value is flat here (everyone ~replacement) — filling your open starter with the "
                              "best available." if sl["flat"] else "Best value at your biggest need right now."),
                "confidence": 60, "agreesWithModel": True, "overrideApplied": False,
                "deterministicAnchor": anchor, "shortlist": shortlist, "urgencyMeta": umeta,
            })

        # ---- LLM synthesis over the NAMED shortlist ----
        system_blocks = coach_system_blocks()
        best_pick_format = {
            "type": "json_schema",
            "schema": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "optimalPick": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string"}, "position": {"type": "string"},
                            "urgency": {"type": "string", "enum": ["pick-now", "lean-now", "can-wait"]},
                            "oneLineWhy": {"type": "string"},
                        },
                        "required": ["name", "position", "urgency", "oneLineWhy"],
                    },
                    "alternatives": {
                        "type": "array",
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"}, "position": {"type": "string"},
                                "urgency": {"type": "string", "enum": ["pick-now", "lean-now", "can-wait"]},
                                "oneLineWhy": {"type": "string"},
                            },
                            "required": ["name", "position", "urgency", "oneLineWhy"],
                        },
                    },
                    "waitSignal": {"type": "string"},
                    "synthesis": {"type": "string"},
                    "confidence": {"type": "integer"},
                },
                "required": ["optimalPick", "alternatives", "waitSignal", "synthesis", "confidence"],
            },
        }
        user_payload = {
            "meta": {"pickNumber": overall_pick, "round": sl["meta"]["round"], "teams": teams,
                     "mySlot": my_slot, "picksUntilNext": sl["meta"]["picksUntilNext"],
                     "nextPick": sl["meta"]["nextPick"],
                     "nextTurnPick": sl["meta"].get("nextTurnPick"),
                     "backToBack": sl["meta"].get("backToBack"), "flat": sl["flat"]},
            "myOpenStarters": sl["meta"].get("openStarters", []),
            "anchor": anchor,
            "whoPicksBeforeMe": [{"team": t["team"], "needs": t["needs"], "tendency": t.get("profile")}
                                 for t in sl["intervening"]],
            "activeRuns": sl["meta"].get("runs", []),
            "shortlist": [{k: c.get(k) for k in ("name", "position", "team", "proj", "adp", "vorp",
                          "tier", "survival", "adjSurvival", "urgency", "fits", "why",
                          "ecr", "sos")} for c in shortlist],
        }
        instruction = (
            "Note: shortlist[].survival = the probability the player is still available at your "
            "NEXT TURN (meta.nextTurnPick), NOT the literal next pick. When meta.backToBack is "
            "true you hold THIS pick and the next one right now — nobody can draft between your "
            "paired picks, so never claim a player 'won't survive' to the paired pick; urgency is "
            "entirely about who survives the wait to meta.nextTurnPick. myOpenStarters is the "
            "AUTHORITATIVE list of your open starting slots — never claim a slot is open or "
            "filled contrary to it. "
            "shortlist[].ecr = FantasyPros expert-consensus OVERALL rank (independent of our "
            "model — a big ecr-vs-our-ranking gap means experts see role/news our projections miss); "
            "sos = season strength-of-schedule, 1=brutal..5=easy. "
            "Pick my OPTIMAL player to draft right now. Choose optimalPick.name and EVERY "
            "alternatives[].name ONLY from shortlist[].name — never invent a player not in the "
            "shortlist. Default to `anchor` unless a clear shortlist signal (a positional run in "
            "whoPicksBeforeMe/activeRuns, a tier cliff on an open required starter, or bye stacking) "
            "justifies overriding it; if you override, explain in synthesis. When meta.flat is true, "
            "VORP is noise (everyone ~replacement) — decide on ceiling, role/opportunity, tier cliffs "
            "among startable players, runs, and which shortlist players the intervening managers take "
            "before nextPick. Give a crisp one-line why for each — every oneLineWhy (including for "
            "players you would NOT pick yourself) must state the AFFIRMATIVE case: the one factual "
            "reason to take that player now. Never write a why that argues against its own player; "
            "your disagreement with the ranking belongs ONLY in synthesis, where it is shown as a "
            "labeled dissent. Also give a waitSignal naming a player you can safely wait on, and a "
            "1-2 sentence synthesis. Context JSON:\n" + json.dumps(user_payload)
        )
        output_config = {"format": best_pick_format}
        if COACH_EFFORT:
            output_config["effort"] = COACH_EFFORT
        resp = client.messages.create(
            model=COACH_MODEL, max_tokens=1024, system=system_blocks,
            output_config=output_config,
            messages=[{"role": "user", "content": instruction}],
        )

        if resp.stop_reason == "refusal":   # degrade to the deterministic anchor
            return jsonify({
                "optimalPick": _card(anchor, None),
                "alternatives": [_card(c["name"], None) for c in shortlist[1:3]],
                "waitSignal": "", "synthesis": "", "confidence": 55,
                "agreesWithModel": True, "overrideApplied": False,
                "deterministicAnchor": anchor, "shortlist": shortlist, "urgencyMeta": umeta,
            })

        parsed = json.loads(next(b.text for b in resp.content if b.type == "text"))
        # validate every LLM-named player against the shortlist; fall back to the anchor.
        opt_name = parsed.get("optimalPick", {}).get("name")
        override = opt_name not in names
        if override:
            opt_name = anchor
        optimal = _card(opt_name, None if override else parsed["optimalPick"].get("oneLineWhy"))
        alts = []
        for a in parsed.get("alternatives", []):
            nm = a.get("name")
            if nm in names and nm != opt_name and all(nm != x["name"] for x in alts):
                alts.append(_card(nm, a.get("oneLineWhy")))
        for c in shortlist:                 # backfill if the LLM named too few valid alternatives
            if len(alts) >= 3:
                break
            if c["name"] != opt_name and all(c["name"] != x["name"] for x in alts):
                alts.append(_card(c["name"], c.get("why")))
        return jsonify({
            "optimalPick": optimal,
            "alternatives": alts[:3],
            "waitSignal": parsed.get("waitSignal", ""),
            "synthesis": parsed.get("synthesis", ""),
            "confidence": parsed.get("confidence"),
            "agreesWithModel": (opt_name == anchor),
            "overrideApplied": override,
            "deterministicAnchor": anchor,
            "shortlist": shortlist,
            "urgencyMeta": umeta,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


_WAIVER_CACHE: dict = {}


@app.route("/api/waivers", methods=["GET"])
def api_waivers():
    """In-season WAIVER WIRE report: live ESPN rosters + free agents valued on
    league-scored ROS projections, lineup-crack upgrades w/ drop suggestions,
    this week's D/ST stream by implied opponent total, injury flags, and the
    room's recent moves. Cached 5 min (?force=1 busts; ?week=N overrides)."""
    try:
        from models import waivers as wv
        week = request.args.get("week", type=int)
        force = request.args.get("force") == "1"
        key = f"w{week or 'auto'}"
        hit = _WAIVER_CACHE.get(key)
        if hit and not force and time.time() - hit["at"] < 300:
            return jsonify(hit["data"])
        rep = wv.report(2026, week)
        if not rep.get("error"):
            _WAIVER_CACHE[key] = {"at": time.time(), "data": rep}
        return jsonify(rep)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    """Free-form mid-draft Q&A. The user asks anything ('why LaPorta over Pitts?',
    'should I take a QB now or wait?') and Claude answers grounded in the LIVE draft
    state: the pick engine's shortlist/anchor/survival at the current pick, the roster,
    recent picks, and the best remaining players by position. Multi-turn: the client
    sends recent chat history back so follow-ups keep their context."""
    try:
        from models import pick_engine

        body = request.get_json(force=True)
        if client is None:
            return jsonify({"error": "ANTHROPIC_API_KEY not set on the server"}), 503
        question = (body.get("question") or "").strip()
        if not question:
            return jsonify({"error": "empty question"}), 400

        my_roster = body.get("myRoster", {}) or {}
        drafted = body.get("drafted", []) or []
        picks = body.get("picks", []) or []
        meta = body.get("meta", {}) or {}
        overall_pick = int(meta.get("pickNumber") or 1)
        teams = int(meta.get("teams") or 10)
        rounds = int(meta.get("rounds") or 18)
        my_slot = body.get("mySlot")
        bench = body.get("bench") or []
        team_order = body.get("teamOrder") or []

        rows = _load_board_rows()
        drafted_set = set(drafted)

        # The engine's live view — same machinery the verdict card runs on, so the chat
        # can explain exactly what the model is doing. Non-fatal if it can't run.
        engine = {}
        try:
            team_rosters = {}
            for pk in picks:
                t = pk.get("team")
                if t:
                    team_rosters.setdefault(t, []).append(
                        {"name": pk.get("name"), "position": pk.get("pos")})
            sl = pick_engine.shortlist(rows, drafted_set, my_roster, picks, overall_pick,
                                       teams=teams, team_order=team_order,
                                       team_rosters=team_rosters, my_slot=my_slot,
                                       rounds=rounds, bench=bench,
                                       profiles=load_profiles_by_owner(active_only=True),
                                       alias_map=load_alias_map())
            engine = {
                "anchor": sl["anchor"],
                "flatMode": sl["flat"],
                "nextPick": sl["meta"]["nextPick"],
                "picksUntilNext": sl["meta"]["picksUntilNext"],
                "nextTurnPick": sl["meta"].get("nextTurnPick"),
                "backToBack": sl["meta"].get("backToBack"),
                "openStarters": sl["meta"].get("openStarters", []),
                "activeRuns": sl["meta"].get("runs", []),
                "whoPicksBeforeMe": [{"team": t["team"], "needs": t["needs"],
                                      "tendency": t.get("profile")} for t in sl["intervening"]],
                "shortlist": [{k: c.get(k) for k in ("name", "position", "team", "proj",
                              "adp", "vorp", "tier", "survival", "urgency", "fits", "why",
                              "ecr", "sos")} for c in sl["shortlist"]],
            }
        except Exception:
            traceback.print_exc()

        # Best remaining by position — lets the chat answer 'who's left at TE?' style
        # questions beyond the shortlist. norm-matched against the drafted list.
        from models.scoring import norm_name
        gone = {norm_name(n) for n in drafted_set}
        top_avail = {}
        for r in rows:
            pos = r.get("pos")
            if norm_name(r.get("name", "")) in gone:
                continue
            bucket = top_avail.setdefault(pos, [])
            if len(bucket) < 6:
                try:
                    bucket.append({"name": r["name"], "team": r.get("team"),
                                   "proj": round(float(r.get("league_pts") or 0)),
                                   "vorp": round(float(r.get("vorp") or 0), 1),
                                   "adp": float(r["adp"]) if r.get("adp") else None,
                                   "fp_adp": float(r["fp_adp"]) if r.get("fp_adp") else None,
                                   "ecr": float(r["ecr"]) if r.get("ecr") else None})
                except (ValueError, KeyError):
                    continue

        # Any player the user NAMES gets his full board row attached — the
        # top-6-per-position window missed deep names (KC Concepcion, Jayden
        # Higgins) and the coach rightly refused to invent numbers for them.
        import re as _re
        qtext = " ".join([question] + [str(m.get("content") or "")
                                       for m in (body.get("history") or [])]).lower()
        qnorm = " ".join(_re.sub(r"[^a-z0-9 ]", " ", qtext).split())
        _SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}
        mentioned = []
        for r in rows:
            nm = str(r.get("name") or "")
            toks = _re.sub(r"[^a-z0-9 ]", " ", nm.lower()).split()
            if not toks:
                continue
            full = " ".join(toks)                                   # 'marvin harrison jr'
            core = [t for t in toks if t not in _SUFFIX] or toks
            core_full = " ".join(core)                              # 'marvin harrison'
            last = core[-1]                                         # real surname, not 'jr'
            if (full in qnorm or core_full in qnorm
                    or (len(last) >= 5 and _re.search(r"\b" + _re.escape(last) + r"\b", qnorm))):
                row = {"name": nm, "pos": r.get("pos"), "team": r.get("team"),
                       "alreadyDrafted": norm_name(nm) in gone}
                for k in ("league_pts", "vorp", "rank", "adp", "ecr", "fp_adp",
                          "fp_best", "fp_worst", "fp_std", "fp_up", "fp_bust", "wk25"):
                    v = r.get(k)
                    if v not in (None, ""):
                        try:
                            row[k] = round(float(v), 1)
                        except ValueError:
                            row[k] = v
                mentioned.append(row)
            if len(mentioned) >= 10:
                break

        state = {
            "meta": {"pickNumber": overall_pick, "round": (overall_pick - 1) // teams + 1,
                     "teams": teams, "rounds": rounds, "mySlot": my_slot},
            "myRoster": my_roster, "myBench": bench,
            "recentPicks": picks[-12:],
            "engine": engine,
            "bestAvailableByPosition": top_avail,
            "mentionedPlayers": mentioned,
        }

        # Bounded multi-turn history from the client (keeps follow-ups coherent).
        history = []
        for m in (body.get("history") or [])[-10:]:
            role, content = m.get("role"), (m.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                history.append({"role": role, "content": content})
        while history and history[0]["role"] != "user":
            history.pop(0)

        instruction = (
            "You are answering a free-form question from the user MID-DRAFT. Ground every claim "
            "in the DRAFT STATE JSON below — cite the actual numbers (VORP, survival %, ADP/market, "
            "tier, projections) that drive your answer, and never invent players or stats not in "
            "the data. `engine` is the DraftIQ pick engine's live view: `anchor` is the model's "
            "current #1, `shortlist[].survival` is each player's probability of still being "
            "available at the user's NEXT TURN (engine.nextTurnPick — NOT the literal next pick: "
            "when engine.backToBack is true the user holds this pick AND the next one right now, "
            "so nobody can take a player between them; never say a player is 'gone by' a paired "
            "pick — urgency is only about surviving the wait to nextTurnPick), and `flatMode` "
            "means VORP is currently noise. `engine.openStarters` is the authoritative list of "
            "open starting slots — never contradict it. "
            "If the question is about why the model/coach recommends something, explain using "
            "those fields. `mentionedPlayers` carries the full board row (our rank/vorp + market "
            "fp_adp/ecr + expert spread fp_best/fp_std when available) for every player named in "
            "the conversation — use it so you never have to say a named player isn't in the data. "
            "Answer in plain prose (no headers, no markdown tables), <=160 words "
            "unless the question genuinely needs more, and end with a clear recommendation when "
            "one is asked for.\n\nDRAFT STATE JSON:\n" + json.dumps(state)
            + "\n\nQUESTION: " + question
        )
        kwargs = {}
        if COACH_EFFORT:
            kwargs["output_config"] = {"effort": COACH_EFFORT}
        resp = client.messages.create(
            model=COACH_MODEL, max_tokens=1024, system=coach_system_blocks(),
            messages=history + [{"role": "user", "content": instruction}], **kwargs,
        )
        if resp.stop_reason == "refusal":
            return jsonify({"answer": "Coach declined that one — try rephrasing.",
                            "refused": True})
        answer = "".join(b.text for b in resp.content if b.type == "text").strip()
        return jsonify({"answer": answer,
                        "anchor": engine.get("anchor"),
                        "pick": overall_pick})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ---------------------------
# Overnight features — GHOST DRAFT / TITLE ODDS / SPY / WHEEL PLAN
# All deterministic (no LLM): draft night costs nothing extra.
# ---------------------------
def _sim_ctx(body):
    """Common request unpacking for the simulation-family endpoints."""
    meta = body.get("meta", {}) or {}
    return {
        "picks": body.get("picks", []) or [],
        "team_order": body.get("teamOrder") or [],
        "overall": int(meta.get("pickNumber") or 1),
        "teams": int(meta.get("teams") or 10),
        "rounds": int(meta.get("rounds") or 18),
        "my_slot": int(body.get("mySlot") or 0) or None,
    }


@app.route("/api/sim/opponents", methods=["POST"])
def sim_opponents():
    """GHOST DRAFT: play every opponent pick forward (manager priors + ADP noise +
    roster need) until the user's seat is back on the clock, or to the end."""
    try:
        from models import opponent_ai
        body = request.get_json(force=True)
        c = _sim_ctx(body)
        rows = _load_board_rows()
        if not rows:
            return jsonify({"error": "board not built"}), 503
        res = opponent_ai.simulate(
            rows, c["picks"], c["team_order"], c["overall"], c["my_slot"] or 0,
            teams=c["teams"], rounds=c["rounds"],
            profiles=load_profiles_by_owner(active_only=True), alias_map=load_alias_map(),
            seed=body.get("seed"),
            stop_at_my_pick=(body.get("mode") != "toEnd"))
        return jsonify(res)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/odds", methods=["POST"])
def api_odds():
    """TITLE ODDS: Monte Carlo the season (league-calibrated weekly variance,
    real 2026 playoff format) from the current rosters + remaining board."""
    try:
        from models import season_sim
        from models.pick_engine import normalize_pos
        body = request.get_json(force=True)
        team_rosters = body.get("teamRosters", {}) or {}
        drafted = {str(n) for n in (body.get("drafted") or [])}
        rows = _load_board_rows()
        if not rows:
            return jsonify({"error": "board not built"}), 503
        by_key, by_name = {}, {}
        for r in rows:
            try:
                pts = float(r.get("league_pts") or 0)
            except (TypeError, ValueError):
                pts = 0.0
            pos = normalize_pos(r.get("pos"))
            by_key.setdefault((r["name"], pos), pts)
            by_name.setdefault(r["name"], pts)

        def lookup(name, pos):
            return by_key.get((name, pos)) or by_name.get(name) or 0.0

        avail = [r for r in rows if r["name"] not in drafted]
        odds = season_sim.title_odds(team_rosters, avail, lookup,
                                     n_sims=int(body.get("sims") or 1000))
        return jsonify({"odds": odds, "weeklySd": round(season_sim.weekly_sd(), 1)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/spy", methods=["POST"])
def api_spy():
    """SPY dossier: one opponent's tendency profile + their model-predicted next
    picks (probabilities from the same engine that drives GHOST DRAFT)."""
    try:
        from models import opponent_ai
        body = request.get_json(force=True)
        c = _sim_ctx(body)
        target = body.get("team")
        rows = _load_board_rows()
        if not rows or not target:
            return jsonify({"error": "board not built or no team named"}), 400
        avail, states, _ = opponent_ai.build_state(rows, c["picks"], c["team_order"], c["teams"])
        st = states.get(target)
        if st is None:
            return jsonify({"error": f"unknown team {target}"}), 400
        owner = load_alias_map().get(target)
        prof = load_profiles_by_owner(active_only=False).get(owner) if owner else None
        rnd = (c["overall"] - 1) // c["teams"] + 1
        preds = opponent_ai.predict(st, avail, rnd, c["rounds"], prof, c["picks"], c["teams"])
        return jsonify({
            "team": target, "owner": owner,
            "profile": ({k: prof.get(k) for k in
                         ("summary", "confidence", "titles", "avg_finish", "drafts",
                          "qb_first_round_avg", "rb_in_first4", "wr_in_first4")}
                        if prof else None),
            "needs": st.open_starters(),
            "predictions": preds,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/wheel-plan", methods=["POST"])
def api_wheel_plan():
    """WHEEL planner: best two-pick pair at the turn + Monte-Carlo survival
    forecast for the 18-pick wait (models/wheel.py)."""
    try:
        from models import wheel
        body = request.get_json(force=True)
        c = _sim_ctx(body)
        rows = _load_board_rows()
        if not rows:
            return jsonify({"error": "board not built"}), 503
        res = wheel.plan(rows, body.get("drafted", []) or [], body.get("myRoster", {}) or {},
                         c["picks"], c["overall"], teams=c["teams"],
                         team_order=c["team_order"],
                         team_rosters=body.get("teamRosters"), my_slot=c["my_slot"],
                         rounds=c["rounds"], bench=body.get("bench") or [],
                         profiles=load_profiles_by_owner(active_only=True),
                         alias_map=load_alias_map())
        return jsonify(res)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/espn/draft", methods=["GET"])
def espn_draft():
    """LIVE DRAFT SYNC: the real ESPN draft, mapped onto DraftIQ's board + UI team
    names (models/espn_sync.py). Poll this on draft night to auto-log every pick.
    ?year=2025 replays last season's completed draft (the pipeline's test fixture)."""
    try:
        from models import espn_sync
        year = int(request.args.get("year", 2026))
        rows = _load_board_rows()
        return jsonify(espn_sync.get_draft(rows, year))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"connected": False, "error": str(e)}), 502


@app.route("/api/sim/tournament", methods=["POST"])
def sim_tournament():
    """WAR GAMES: N complete drafts from the CURRENT state (model drives your picks,
    opponent AI drives theirs), each finished season Monte-Carlo'd. Returns your
    title-odds distribution + which early position sequences actually win."""
    try:
        from models import tournament
        body = request.get_json(force=True) or {}
        c = _sim_ctx(body)
        rows = _load_board_rows()
        if not rows:
            return jsonify({"error": "board not built"}), 503
        res = tournament.run(
            rows, c["picks"], c["team_order"], c["overall"],
            my_slot=c["my_slot"] or 10, teams=c["teams"], rounds=c["rounds"],
            n=max(4, min(int(body.get("n") or 12), 24)),
            profiles=load_profiles_by_owner(active_only=True), alias_map=load_alias_map())
        return jsonify(res)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/health", methods=["GET"])
def api_health():
    """PREFLIGHT: is every system draft-ready? Board freshness, feed sanity,
    keys, ESPN connectivity, coach. Cheap checks; ESPN/feed hit the network."""
    import datetime
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        st = os.stat(BOARD_PATH)
        age_h = (time.time() - st.st_mtime) / 3600
        rows = _load_board_rows()
        n_dst = sum(1 for r in rows if r.get("pos") == "DST")
        n_idp = sum(1 for r in rows if r.get("pos") == "IDP")
        n_skill = sum(1 for r in rows if r.get("pos") in ("QB", "RB", "WR", "TE"))
        add("BOARD", len(rows) > 1000 and n_dst >= 30 and n_idp >= 300 and n_skill >= 400,
            f"{len(rows)} rows · {n_skill} skill · {n_idp} IDP · {n_dst} D/ST · built {age_h:.0f}h ago"
            + (" · STALE, rebuild before drafting" if age_h > 96 else ""))
    except Exception as e:
        add("BOARD", False, str(e))
    try:
        # IDP now comes from ESPN, so SportsData only has to carry the skill positions.
        from models.build_board import check_idp_feed, fetch_projections
        raw = fetch_projections("2026REG")
        skill = [r for r in raw if (r.get("Position") or "") in ("QB", "RB", "WR", "TE", "K")]
        ok_skill = len(skill) > 300 and max((float(r.get("FantasyPointsPPR") or 0)) for r in skill) > 150
        idp_problems = check_idp_feed(raw)
        add("SPORTSDATA (skill)", ok_skill,
            f"{len(skill)} skill projections look sane"
            + ("; its IDP is still broken but we no longer use it" if idp_problems else ""))
    except Exception as e:
        add("SPORTSDATA (skill)", False, f"unreachable: {e}")
    try:
        from models import espn_proj
        idp = espn_proj.get(2026)
        top = idp[0] if idp else None
        add("ESPN IDP PROJ", len(idp) > 300,
            f"{len(idp)} defenders · top {top['name']} {top['points']:.0f} pts" if top
            else "no IDP projections cached")
    except Exception as e:
        add("ESPN IDP PROJ", False, f"unavailable: {str(e)[:70]}")
    try:
        from models import espn_sync
        lg = espn_sync.STATE.league(2026)
        n = len(getattr(lg, "draft", None) or [])
        add("ESPN LIVE SYNC", True, f"connected to '{getattr(lg.settings,'name','?')}' · {n} picks logged")
    except Exception as e:
        add("ESPN LIVE SYNC", False, f"connect failed: {str(e)[:90]}")
    try:
        from models import week1_odds
        d = week1_odds.dst_ranks(2026, 1)
        top = next(iter(d.values()), None)
        add("WEEK-1 ODDS", len(d) >= 24,
            f"{len(d)}/32 defenses have a week-1 line · best matchup {top['team']} vs {top['opp']} "
            f"({top['impliedOppTotal']} implied)" if top else "no lines posted yet")
    except Exception as e:
        add("WEEK-1 ODDS", False, f"unavailable: {str(e)[:70]}")
    add("AI COACH", client is not None,
        f"Anthropic key set · model {COACH_MODEL}" if client else "ANTHROPIC_API_KEY missing — deterministic fallbacks only")
    try:
        profs = load_manager_profiles(active_only=True)
        add("OPPONENT INTEL", len(profs) >= 8, f"{len(profs)} active manager profiles")
    except Exception as e:
        add("OPPONENT INTEL", False, str(e))
    ok = all(c["ok"] for c in checks)
    return jsonify({"ok": ok, "at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "checks": checks})


# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
