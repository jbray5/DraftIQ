"""In-season WAIVER WIRE engine — decision-grade, not homework.

MEASURED (2026-09-16, 84 team-seasons 2019-2025): the in-season edges in this
league are real but modest, and QUALITY beats volume. Lineup capture (started
pts / hindsight-optimal) is the clearest signal — Spearman +0.39 with
points-for (partly mechanical), −0.19 with finish, and the whole room lives
in an 87-91% band, so a few capture points ≈ tens of season points. Roster
churn: champions average 22 adds vs the field's 17 and five of seven were
top-half active, but the correlation with finish is only −0.14 (ns) — the
2020 champ made 16 adds. Streaming BREADTH is uncorrelated with finish
(−0.01); the validated D/ST edge is matchup SELECTION (+55/season), not
changing defenses often. Hence this module's posture: netVorp-priced claims,
optimizer-driven lineups, matchup-priced streams — never churn for its own
sake. The contract: ACTIONS are things you should actually do — everything
else is context. If there is nothing to do, it says ALL CLEAR and means it.

  * ACTIONS: injury-driven claims (the engine pulls each flagged player's real
    injury note from ESPN's athlete API and prices the replacement move),
    lineup-cracking upgrades, and D/ST stream switches. Each carries the full
    move: add → drop → netVorp, plus the WHY with the fetched news line.
  * WATCHLIST: same-position bench upgrades with a real job (dart swaps at
    positions you actually roster thin). Backup-QB hoarding never shows.
  * Stream verdict collapses to one line when it's HOLD.
  * ROOM ACTIVITY: every pull appends to data/processed/waiver_log.jsonl so
    opponent add/drop tendencies accumulate for later analysis + trade intel.

Everything is valued on TRUE rest-of-season projections — ESPN's league-scored
season aggregate MINUS already-banked actuals (see _row: backtested, the raw
aggregate loses ~6pts of decision accuracy and decays all season). Skill/IDP/
K/DST share one honest scale. In-season replacement = the 5th-best free agent
at the position (the wire itself).
netVorp of a move = (add.proj − wire[add.pos]) − (drop.proj − wire[drop.pos]).

Run `python models/waivers.py` for the CLI report; served at /api/waivers.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "models"))

try:
    from espn_proj import _league
    from inseason import bucket, optimal_lineup, slot_spec, waiver_targets
    import week1_odds
except ImportError:  # pragma: no cover
    from models.espn_proj import _league
    from models.inseason import bucket, optimal_lineup, slot_spec, waiver_targets
    from models import week1_odds

CFG = json.loads((ROOT / "data" / "league_config.json").read_text(encoding="utf-8"))
LOG = ROOT / "data" / "processed" / "waiver_log.jsonl"
OWN_LOG = ROOT / "data" / "processed" / "ownership_history.jsonl"
BAD_STATUS = {"OUT", "INJURY_RESERVE", "IR", "SUSPENSION", "PHYSICALLY_UNABLE_TO_PERFORM"}
# Decision thresholds in POINTS PER REMAINING WEEK (multiplied by weeks left at
# runtime). Fixed ROS constants were noise-permeable in September and unreachable
# by December — the wire went quiet exactly when churn decides titles.
LINEUP_CRACK_PW = 0.5    # a claim must add at least this per week to your lineup
WATCH_PW = 0.7           # a bench dart must beat the incumbent by this per week
FA_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST", "LB", "DE", "DT", "CB", "S")
ATH = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{pid}"

_LG_CACHE: dict = {}


def _lg(season: int):
    """One espn-api League per season, reused for 120s — report() used to build
    it TWICE per call, and each construction re-fetches the whole pro player map."""
    hit = _LG_CACHE.get(season)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    lg = _league(season)
    _LG_CACHE[season] = (time.time(), lg)
    return lg


def _row(p) -> dict:
    """espn_api Player -> plain dict on the league-scored TRUE rest-of-season scale.

    ESPN's projected_total_points is the season AGGREGATE (scoringPeriod 0) — it
    carries already-banked production, so used raw it credits players for points
    you can no longer roster. proj here = aggregate − banked actuals, clamped ≥0.
    BACKTESTED (2026-09-15, 2024+2025 box_players, wks 3/5/8/11, 23,855 same-pos
    decision pairs): pure ROS ranks actual remaining points at 90.8% pairwise
    accuracy vs 84.5% for the aggregate, and the gap WIDENS with each week banked
    (wk 11: ~89% vs ~78%; Spearman .91 vs .83). Do not revert to the aggregate."""
    try:
        own = round(float(getattr(p, "percent_owned", None)), 1)
    except (TypeError, ValueError):
        own = None
    proj_total = float(getattr(p, "projected_total_points", 0) or 0)
    banked = float(getattr(p, "total_points", 0) or 0)
    return {
        "playerId": getattr(p, "playerId", None),
        "name": getattr(p, "name", None),
        "pos": bucket(getattr(p, "position", "") or ""),
        "espnPos": getattr(p, "position", None),
        "team": getattr(p, "proTeam", None),
        "proj": round(max(0.0, proj_total - banked), 1),
        "banked": round(banked, 1),
        "injury": (str(getattr(p, "injuryStatus", "") or "").upper() or None),
        "own": own,     # % of ESPN leagues rostering him — the market's opinion
        "lineupSlot": getattr(p, "lineupSlot", None),   # 'IR' matters: not startable
    }


def _log_ownership(fas: list[dict]) -> None:
    """One line per day: {date, own:{playerId: pct}} for the FA pool, so tomorrow's
    report can show which way the market is running. Cheap in-season analog of the
    draft-era market-premium signal (validated as a dart TIEBREAK, never a ranker)."""
    try:
        today = time.strftime("%Y-%m-%d")
        if OWN_LOG.exists():
            lines = OWN_LOG.read_text(encoding="utf-8").splitlines()
            if lines and json.loads(lines[-1]).get("date") == today:
                return
        snap = {str(r["playerId"]): r["own"] for r in fas
                if r.get("playerId") is not None and r.get("own") is not None}
        if snap:
            with open(OWN_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"date": today, "own": snap}) + "\n")
    except Exception:
        pass


def _own_deltas() -> dict:
    """{playerId(str): pct} from the most recent snapshot BEFORE today."""
    try:
        today = time.strftime("%Y-%m-%d")
        for line in reversed(OWN_LOG.read_text(encoding="utf-8").splitlines()):
            d = json.loads(line)
            if d.get("date") != today:
                return d.get("own") or {}
    except Exception:
        pass
    return {}


def _my_ui_and_owner() -> tuple[str, str]:
    aliases = json.loads((ROOT / "data" / "team_aliases.json").read_text(encoding="utf-8"))
    for ui, owner in aliases.items():
        if ui.lower() in ("jray", "ray"):
            return ui, str(owner).lower()
    return "JRay", "jbray5"


def _injury_news(pid) -> dict | None:
    """The engine does the research: real injury note from ESPN's athlete API."""
    if not pid:
        return None
    try:
        a = requests.get(ATH.format(pid=pid), timeout=8).json()
        ath = a.get("athlete", a)
        for inj in (ath.get("injuries") or []):
            txt = (inj.get("shortComment") or inj.get("longComment") or "").strip()
            if txt:
                return {"note": txt[:320], "status": inj.get("status"),
                        "date": str(inj.get("date") or "")[:10]}
        o = requests.get(ATH.format(pid=pid) + "/overview", timeout=8).json()
        news = o.get("news") or []
        if isinstance(news, list) and news:
            n = news[0]
            return {"note": ((n.get("headline") or "") + " — "
                             + (n.get("description") or ""))[:320],
                    "date": str(n.get("published") or "")[:10]}
    except Exception:
        pass
    return None


def snapshot(season: int = 2026) -> dict:
    """Live league state: my roster, all rosters, deduped free agents."""
    lg = _lg(season)
    ui_name, my_owner = _my_ui_and_owner()
    my_team, teams = None, []
    for t in lg.teams:
        owners = [str(o.get("displayName") or "").lower()
                  for o in (getattr(t, "owners", None) or []) if isinstance(o, dict)]
        roster = [_row(p) for p in (getattr(t, "roster", None) or [])]
        rec = {"teamName": getattr(t, "team_name", None), "owners": owners,
               "waiverRank": getattr(t, "waiver_rank", None), "roster": roster}
        teams.append(rec)
        if my_owner in owners or "sclsu" in str(rec["teamName"] or "").lower():
            my_team = rec
    seen, fas = set(), []
    for pos in FA_POSITIONS:
        try:
            for p in lg.free_agents(size=200, position=pos):
                pid = getattr(p, "playerId", None)
                own = getattr(p, "position", None)
                if pid is None or pid in seen or (own not in FA_POSITIONS):
                    continue
                r = _row(p)
                if r["proj"] <= 0:
                    continue
                seen.add(pid)
                fas.append(r)
        except Exception:
            continue
    fas.sort(key=lambda r: -r["proj"])
    _log_ownership(fas)          # daily archive so tomorrow's report has a delta
    week = max(1, int(getattr(lg, "current_week", 1) or 1))
    return {"week": week, "myTeam": my_team, "teams": teams, "freeAgents": fas}


def _log_activity(moves: list[dict]) -> None:
    """Accumulate the room's moves for tendency analysis + trade intel.
    Keyed by (owner-or-team, action, player, DATE): the dateless key collapsed
    repeat streams (the exact behavior we're tracking), and owner handles
    survive this room's troll-renames where team names fragment."""
    try:
        seen = set()
        if LOG.exists():
            for line in LOG.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                    seen.add((d.get("owner") or d.get("team"), d.get("action"),
                              d.get("player"), d.get("loggedAt")))
                except json.JSONDecodeError:
                    continue
        today = time.strftime("%Y-%m-%d")
        with open(LOG, "a", encoding="utf-8") as fh:
            for m in moves:
                key = (m.get("owner") or m.get("team"), m.get("action"),
                       m.get("player"), today)
                if key not in seen:
                    fh.write(json.dumps({**m, "loggedAt": today}) + "\n")
                    seen.add(key)
    except OSError:
        pass


def report(season: int = 2026, week: int | None = None) -> dict:
    snap = snapshot(season)
    if not snap["myTeam"]:
        return {"error": "could not identify your team in the league"}
    week = week or snap["week"]
    my = snap["myTeam"]["roster"]
    # IR-slotted players can't start and don't occupy a usable roster spot:
    # exclude them from lineup math and from drop suggestions (they still get
    # injury flags below). `work` is the running roster — each accepted ACTION
    # applies its add/drop so the NEXT action's drop suggestion stays coherent
    # instead of three claims all naming the same victim.
    my_active = [p for p in my if (p.get("lineupSlot") or "") != "IR"]
    work = list(my_active)
    spec = slot_spec(CFG["2026"])
    fas = snap["freeAgents"]
    rem = max(1, 19 - week)      # NFL weeks left (ROS projections span these)

    # in-season replacement = 5th-best FA at the position (the wire next week)
    by_pos: dict[str, list[float]] = {}
    for r in fas:
        by_pos.setdefault(r["pos"], []).append(r["proj"])
    repl = {pos: (lst[4] if len(lst) > 4 else lst[-1]) for pos, lst in by_pos.items()}

    lineup = optimal_lineup(my_active, spec)

    def _vorp(p) -> float:
        return round((p["proj"] or 0) - repl.get(p["pos"], 0), 1)

    def _best_drop(add_row, protect: set[str] = frozenset()):
        after = optimal_lineup(work + [add_row], spec)
        keep = {p["name"] for _, p in after["starters"]} | set(protect)
        bench = [p for p in work if p["name"] not in keep]
        return min(bench, key=_vorp) if bench else None

    def _apply(mv) -> None:
        """Commit an accepted action to the working roster so the next action's
        drop suggestion is computed against reality, not the original roster."""
        if mv.get("drop"):
            work[:] = [p for p in work if p["name"] != mv["drop"]["name"]]
        a = mv["add"]
        work.append({"name": a["name"], "pos": a["pos"], "team": a.get("team"),
                     "proj": a["proj"], "injury": None, "lineupSlot": "BE"})

    own_prev = _own_deltas()

    def _heat(row) -> dict:
        """Market heat: ESPN-wide roster% + day-over-day move. DISPLAY-ONLY tiebreak
        (the draft-validated role for market signals) — never re-ranks anything."""
        own = row.get("own")
        prev = own_prev.get(str(row.get("playerId") or ""))
        delta = round(own - prev, 1) if (own is not None and prev is not None) else None
        return {"own": own, "ownDelta": delta}

    def _move(add, protect: set[str] = frozenset()):
        drop = _best_drop({k: add[k] for k in ("name", "pos", "proj")}, protect)
        net = round(_vorp(add) - (_vorp(drop) if drop else 0), 1)
        note = None
        if drop and drop.get("injury") in BAD_STATUS:
            note = (f"{drop['name']} is {drop['injury']} — if an IR slot is open, "
                    "stash him there instead and drop your lowest healthy depth")
        return {"add": {**{k: add[k] for k in ("name", "pos", "team", "proj")}, **_heat(add)},
                "drop": ({**{k: drop[k] for k in ("name", "pos", "team", "proj")},
                          "vsWire": _vorp(drop)} if drop else None),
                "dropNote": note,
                "vsWire": _vorp(add), "netVorp": net,
                "netVorpWk": round(net / rem, 2)}

    actions: list[dict] = []
    claimed: set[str] = set()

    # ---- 1. injury-driven claims: the engine reads the news itself ----
    flags = []
    for p in my:
        if p.get("injury") not in BAD_STATUS:
            continue
        news = _injury_news(p.get("playerId")) or {}
        flags.append({**{k: p[k] for k in ("name", "pos", "team", "proj", "injury")},
                      "news": news.get("note"), "newsDate": news.get("date")})
        # would his position's best FA start if he can't go? (proj->0 for him)
        without = [({**q, "proj": 0.0} if q["name"] == p["name"] else q) for q in work]
        cands = [f for f in fas if f["pos"] == p["pos"]][:8]
        best, best_gain = None, 0.0
        base = optimal_lineup(without, spec)["total"]
        for f in cands:
            gain = optimal_lineup(without + [f], spec)["total"] - base
            if gain > best_gain:
                best, best_gain = f, gain
        if best and best_gain >= LINEUP_CRACK_PW * rem and best["name"] not in claimed:
            mv = _move(best, protect={p["name"]})   # never drop the injured guy blind
            claimed.add(best["name"])
            actions.append({
                "type": "CLAIM", "urgency": "act before waivers clear",
                "why": (f"{p['name']} is {p['injury']}"
                        + (f" — {news['note']}" if news.get("note") else "")
                        + f". {best['name']} is the best wire {p['pos']} and starts while he's out."),
                **mv})
            _apply(mv)

    # ---- 2. straight lineup-crackers (healthy roster, FA is just better) ----
    targets = waiver_targets(work, fas[:120], repl, spec, top=25)
    for t in targets:
        if t["lineup_gain"] >= LINEUP_CRACK_PW * rem and t["name"] not in claimed:
            mv = _move(t)
            claimed.add(t["name"])
            actions.append({"type": "CLAIM", "urgency": "clear upgrade",
                            "why": (f"{t['name']} beats your current starter — "
                                    f"+{round(t['lineup_gain'] / rem, 1)}/wk "
                                    f"(+{t['lineup_gain']} ROS) before any injury help."),
                            **mv})
            _apply(mv)

    # ---- 3. D/ST stream switch ----
    stream: dict = {"week": week}
    try:
        ranks = week1_odds.dst_ranks(season, week)
        lines_stale = any(v.get("stale") for v in ranks.values())
        if lines_stale:
            stream["linesStale"] = True

        def _tc(r):
            return str(r.get("team") or "").upper()

        my_dsts = [p for p in work if p["pos"] == "DST"]
        mine = [{"name": p["name"], **(ranks.get(_tc(p)) or {})} for p in my_dsts]
        fa_dsts = sorted(({"name": r["name"], "pos": "DST", "team": r["team"],
                           "proj": r["proj"], "playerId": r.get("playerId"),
                           "own": r.get("own"), **(ranks.get(_tc(r)) or {})}
                          for r in fas if r["pos"] == "DST"),
                         key=lambda x: x.get("w1Rank") or 99)
        my_best = min((m.get("w1Rank") or 99) for m in mine) if mine else 99
        fa_best = fa_dsts[0] if fa_dsts else None
        stream.update({"myDst": mine, "hold": True,
                       "line": f"D/ST: HOLD — yours has the better matchup (#{my_best})"})
        if fa_best and (fa_best.get("w1Rank") or 99) + 2 < my_best:
            stream["hold"] = False
            stream["line"] = (f"STREAM {fa_best['name']} (matchup #{fa_best['w1Rank']}) "
                              f"over yours (#{my_best})"
                              + (" · ⚠ Vegas lines are a cached copy" if lines_stale else ""))
            mv = _move(fa_best)
            # claim ladder: the next-best FA matchups, so a lost claim has
            # pre-ranked fallbacks instead of hand math
            mv["ladder"] = [{"name": d["name"], "rank": d.get("w1Rank"),
                             "own": d.get("own")}
                            for d in fa_dsts[1:4]]
            actions.append({"type": "STREAM", "urgency": f"before week {week} locks",
                            "why": (f"Implied-total matchup #{fa_best['w1Rank']} vs your #{my_best} "
                                    "— the validated +55 pts/season play."),
                            **mv})
            _apply(mv)
        # NEXT-WEEK LOOK-AHEAD: a stream costs a roster spot (and maybe waiver
        # priority), so plan it as a 2-week move. Vegas posts lines ~a week out,
        # so this is often empty early in the week — the UI says so rather than
        # guessing. (Switch hysteresis stays the 2-rank heuristic; deriving the
        # backtest-optimal threshold is an open study.)
        try:
            if week + 1 <= 17:
                nxt = week1_odds.dst_ranks(season, week + 1)
                fa_next = sorted(({"name": r["name"], **(nxt.get(_tc(r)) or {})}
                                  for r in fas if r["pos"] == "DST"),
                                 key=lambda x: x.get("w1Rank") or 99)
                my_next = min((((nxt.get(_tc(p)) or {}).get("w1Rank")) or 99)
                              for p in my_dsts) if my_dsts else 99
                if fa_next and fa_next[0].get("w1Rank"):
                    b = fa_next[0]
                    stream["planLine"] = (f"wk{week + 1} look-ahead: best FA "
                                          f"{b['name']} (#{b['w1Rank']}) vs your best #{my_next}"
                                          + (" — could cover both weeks" if b.get("w1Rank", 99) <= 5
                                             and any(d.get("name") == b["name"] for d in fa_dsts[:3]) else ""))
                else:
                    stream["planLine"] = f"wk{week + 1} look-ahead: no Vegas lines posted yet"
        except Exception:
            pass
    except Exception as e:
        stream["line"] = f"stream check unavailable ({e})"

    # ---- WATCHLIST: same-position dart swaps with a real job, nothing else ----
    my_pos_count: dict[str, int] = {}
    for p in my_active:
        my_pos_count[p["pos"]] = my_pos_count.get(p["pos"], 0) + 1
    watch = []
    for t in targets:
        if t["name"] in claimed or t["lineup_gain"] >= LINEUP_CRACK_PW * rem:
            continue
        pos = t["pos"]
        # K/DST stream, never stash. IDP is deliberately ELIGIBLE: measured weekly
        # persistence 0.158 ≈ TE — a hold asset, so a better wire IDP is actionable.
        if pos in ("K", "DST"):
            continue
        if pos == "QB" and my_pos_count.get("QB", 0) >= 2:
            continue                      # no backup-QB hoarding in a 10-team league
        if pos == "TE" and my_pos_count.get("TE", 0) >= 2:
            continue
        if pos == "IDP" and my_pos_count.get("IDP", 0) >= 2:
            continue                      # one starter + one stash max
        mv = _move(t)
        if mv["netVorp"] >= WATCH_PW * rem and mv["drop"] and mv["drop"]["pos"] == pos:
            watch.append({**mv, "why": (f"straight {pos} dart upgrade — same slot, "
                                        f"+{mv['netVorpWk']}/wk (+{mv['netVorp']} ROS)")})
        if len(watch) >= 5:
            break

    # ---- WAIVER-PRIORITY ECONOMICS (this league's real currency: priority,
    # not FAAB). For each action: how many teams AHEAD of us in the order could
    # start the target — if none, he likely clears to free agency and adding
    # post-waivers keeps our priority. Would-start is a roster heuristic, not a
    # mind-reader; clear-to-FA empirics accumulate in waiver_log over time. ----
    NEED_K = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "IDP": 1, "DST": 1, "K": 1}

    def _is_me(trec) -> bool:
        _, my_owner_l = _my_ui_and_owner()
        return (my_owner_l in (trec.get("owners") or [])
                or "sclsu" in str(trec.get("teamName") or "").lower())

    def _would_start_for(roster_rows, add) -> bool:
        pos, k = add["pos"], NEED_K.get(add["pos"], 1)
        vals = sorted((q["proj"] or 0 for q in roster_rows if q["pos"] == pos),
                      reverse=True)
        bar = vals[k - 1] if len(vals) >= k else 0.0
        return (add["proj"] or 0) > bar

    my_rank = next((t.get("waiverRank") for t in snap["teams"] if _is_me(t)), None)
    for a in actions:
        if not my_rank:
            break
        ahead = [t for t in snap["teams"]
                 if not _is_me(t) and (t.get("waiverRank") or 99) < my_rank
                 and _would_start_for(t["roster"], a["add"])]
        a["claim"] = {
            "myPriority": my_rank, "aheadWanting": len(ahead),
            "verdict": (f"BURN-WORTHY — {len(ahead)} team(s) ahead of you could start him"
                        if ahead else
                        "likely clears — nobody ahead starts him; grab post-waivers and keep priority")}
    # ESPN processes YOUR claim list in order and burns priority on the first
    # hit — submit in value order, best per-week gain first
    for i, a in enumerate(sorted(actions, key=lambda x: -(x.get("netVorpWk") or 0)),
                          start=1):
        a["submitOrder"] = i

    # ---- room activity (display + persistent log for tendency analysis) ----
    moves = []
    try:
        lg = _lg(season)
        for act in lg.recent_activity(size=25):
            for team, action, player, bid in getattr(act, "actions", []):
                owner = next((str(o.get("displayName")) for o in
                              (getattr(team, "owners", None) or [])
                              if isinstance(o, dict) and o.get("displayName")), None)
                moves.append({"team": getattr(team, "team_name", None),
                              "owner": owner, "action": action,
                              "player": getattr(player, "name", str(player)),
                              "bid": bid})
    except Exception:
        pass
    _log_activity(moves)

    return {"week": week, "myTeam": snap["myTeam"]["teamName"],
            "myLineupProj": lineup["total"], "allClear": not actions,
            "remainingWeeks": rem, "waiverPriority": my_rank,
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M"),
            "actions": actions, "watchlist": watch, "stream": stream,
            "injuryFlags": flags, "roomActivity": moves[:20]}


# --------------------------------------------------------------------------- #
# In-season portal: MY MATCHUP + START/SIT + PROJECTED STANDINGS
# --------------------------------------------------------------------------- #
def startsit(season: int = 2026, week: int | None = None) -> dict:
    """This week's matchup from live ESPN box scores: my lineup vs the optimal
    lineup on ESPN's league-scored weekly projections, concrete swaps, and a
    win probability vs my opponent (weekly sigma from league history)."""
    try:
        from inseason import optimal_lineup as _opt, slot_spec as _spec, bucket as _b
        import season_sim
    except ImportError:
        from models.inseason import optimal_lineup as _opt, slot_spec as _spec, bucket as _b
        from models import season_sim
    import math
    lg = _lg(season)
    week = week or max(1, int(getattr(lg, "current_week", 1) or 1))
    _, my_owner = _my_ui_and_owner()

    def _owners(t):
        return [str(o.get("displayName") or "").lower()
                for o in (getattr(t, "owners", None) or []) if isinstance(o, dict)]

    def _lineup_rows(lineup):
        rows = []
        for p in (lineup or []):
            # game_played: 0 = not kicked off, 100 = final (espn_api BoxPlayer).
            # A played player can legitimately have 0.0 actual points — gate on
            # game_played, never on the points value.
            played = int(getattr(p, "game_played", 0) or 0)
            rows.append({"name": p.name, "pos": _b(getattr(p, "position", "") or ""),
                         "slot": getattr(p, "slot_position", None),
                         "proj": round(float(getattr(p, "projected_points", 0) or 0), 1),
                         "played": played,
                         "actual": (round(float(getattr(p, "points", 0) or 0), 1)
                                    if played > 0 else None),
                         "injury": (str(getattr(p, "injuryStatus", "") or "").upper() or None)})
        return rows

    for m in lg.box_scores(week):
        for side, opp in (("home", "away"), ("away", "home")):
            team = getattr(m, f"{side}_team", None)
            if team is None or (my_owner not in _owners(team)
                                and "sclsu" not in str(getattr(team, "team_name", "")).lower()):
                continue
            mine = _lineup_rows(getattr(m, f"{side}_lineup", []))
            theirs = _lineup_rows(getattr(m, f"{opp}_lineup", []))
            opp_team = getattr(m, f"{opp}_team", None)
            spec = _spec(CFG["2026"])
            started = [p for p in mine if p["slot"] not in ("BE", "IR")]
            my_total = round(sum(p["proj"] for p in started), 1)
            # optimal excludes IR-slotted players — ESPN won't let you start them,
            # so counting them made both the swaps and the bench leak un-actionable
            opt = _opt([p for p in mine if p["slot"] != "IR"], spec)
            opt_names = {p["name"] for _, p in opt["starters"]}
            cur_names = {p["name"] for p in started}
            # SLOT-CORRECT swaps from the optimizer's own (slot, player) pairs —
            # the old alphabetical zip could emit illegal instructions
            to_start = [(slot, p) for slot, p in opt["starters"]
                        if p["name"] not in cur_names]
            to_sit = [p for p in started if p["name"] not in opt_names]
            swaps = []
            for slot, sp in to_start:
                pick = next((q for q in to_sit if q["pos"] == sp["pos"]), None) \
                    or (to_sit[0] if to_sit else None)
                if pick:
                    to_sit.remove(pick)
                swaps.append({"slot": slot, "start": sp["name"], "startPos": sp["pos"],
                              "sit": (pick or {}).get("name")})
            opp_started = [p for p in theirs if p["slot"] not in ("BE", "IR")]
            opp_total = round(sum(p["proj"] for p in opp_started), 1)

            # LIVE week state. ESPN's own live scoreboard (totalPointsLive /
            # totalProjectedPointsLive via BoxScore.{side}_score/_projected) counts
            # in-progress games correctly; our per-player fallback gates on
            # game_played, a kickoff+3h wall clock that misses live games entirely.
            def _live(rows):
                act = round(sum((p["actual"] or 0.0) for p in rows if p["played"]), 1)
                rem = round(sum(p["proj"] for p in rows if not p["played"]), 1)
                return act, rem, sum(1 for p in rows if p["played"])

            my_act, my_rem, my_np = _live(started)
            opp_act, opp_rem, opp_np = _live(opp_started)
            my_live = round(my_act + my_rem, 1)
            opp_live = round(opp_act + opp_rem, 1)
            live_src = "clock"
            e_act = float(getattr(m, f"{side}_score", 0) or 0)
            e_proj = float(getattr(m, f"{side}_projected", -1) or -1)
            o_act = float(getattr(m, f"{opp}_score", 0) or 0)
            o_proj = float(getattr(m, f"{opp}_projected", -1) or -1)
            if e_proj > 0 and o_proj > 0:
                my_act, my_live = round(e_act, 1), round(e_proj, 1)
                opp_act, opp_live = round(o_act, 1), round(o_proj, 1)
                my_rem = round(my_live - my_act, 1)
                opp_rem = round(opp_live - opp_act, 1)
                live_src = "espn-live"
            sd = season_sim.weekly_sd()
            # variance left in the week: weight by the share of PROJECTED POINTS
            # still to score (an unplayed kicker no longer carries QB variance)
            tot_live = max(1.0, my_live + opp_live)
            frac_rem = max(0.0, (max(my_rem, 0) + max(opp_rem, 0)) / tot_live)
            if frac_rem > 0.005:
                sd_eff = max(1.0, 2 * sd * math.sqrt(frac_rem))
                win = 0.5 * (1 + math.erf((my_live - opp_live) / sd_eff))
            else:                       # week complete: it's just the scoreboard
                win = 1.0 if my_live > opp_live else (0.0 if my_live < opp_live else 0.5)

            # VARIANCE ADVISORY (display-only — the mean-based engine stays the
            # engine). This league is ~2/3 luck (PF↔finish −0.55; 26% of playoff
            # spots go to non-top-6 scorers), so WHEN to take risk is a lever:
            # favorites want the floor side of coin flips, underdogs the ceiling
            # side. A "coin flip" = alternatives within 2 projected pts; the σ
            # proxy is FP's expert-rank stddev (wider disagreement = wider range).
            variance_notes = []
            try:
                if abs(win - 0.5) >= 0.08 and frac_rem > 0.5:
                    try:
                        import fp_weekly as _fpw
                    except ImportError:
                        from models import fp_weekly as _fpw
                    fpw = _fpw.get(week)
                    flexable = ("RB", "WR", "TE")

                    def _std(nm):
                        f = fpw.get(_fpw.norm(nm))
                        return f.get("std") if f else None

                    started_fx = [p for p in started if p["pos"] in flexable]
                    bench_fx = [p for p in mine if p["slot"] == "BE"
                                and p["pos"] in flexable and not p["played"]]
                    dog = win < 0.5
                    for b in bench_fx:
                        for s2 in started_fx:
                            if s2["played"] or abs((b["proj"] or 0) - (s2["proj"] or 0)) > 2:
                                continue
                            sb, ss = _std(b["name"]), _std(s2["name"])
                            if sb is None or ss is None:
                                continue
                            if dog and sb > ss + 2:
                                variance_notes.append(
                                    f"underdog lever: {b['name']} is the wider-range side of a "
                                    f"coin flip with {s2['name']} (expert spread σ{sb} vs σ{ss}) "
                                    "— ceiling plays win from behind")
                            elif not dog and ss > sb + 2:
                                variance_notes.append(
                                    f"favorite lever: {b['name']} is the steadier side of a "
                                    f"coin flip with {s2['name']} (expert spread σ{sb} vs σ{ss}) "
                                    "— favorites bank floors")
                    variance_notes = variance_notes[:2]
            except Exception:
                pass
            return {"week": week,
                    "me": {"team": getattr(team, "team_name", None), "current": started,
                           "bench": [p for p in mine if p["slot"] in ("BE", "IR")],
                           "currentTotal": my_total, "optimalTotal": opt["total"],
                           "optimalNames": sorted(opt_names),
                           "actualSoFar": my_act, "remainingProj": my_rem,
                           "liveExp": my_live, "playedStarters": my_np,
                           "nStarters": len(started)},
                    "liveSource": live_src,
                    "fetchedAt": time.strftime("%Y-%m-%d %H:%M"),
                    "varianceNotes": variance_notes,
                    "swaps": swaps, "benchLeak": round(opt["total"] - my_total, 1),
                    "opponent": {"team": getattr(opp_team, "team_name", None),
                                 "projTotal": opp_total,
                                 "lineup": opp_started,
                                 "bench": [p for p in theirs if p["slot"] in ("BE", "IR")],
                                 "actualSoFar": opp_act, "remainingProj": opp_rem,
                                 "liveExp": opp_live, "playedStarters": opp_np,
                                 "nStarters": len(opp_started)},
                    "winProb": round(win, 3)}
    return {"error": f"no matchup found for you in week {week}"}


def season_odds(season: int = 2026) -> dict:
    """PROJECTED STANDINGS from live rosters — judged TWICE: once by ESPN's ROS
    projections, once by OUR draft-board blend. Same Monte Carlo both times;
    only the projection source changes. Where the two judges disagree about a
    player is the TRADE MAP: sell what ESPN overrates (the room drafts and
    trades off ESPN's numbers), buy what it underrates. Title% carries ~±1pt of
    MC noise at 1000 sims — ordinal ranks inside a tight cluster are ties."""
    try:
        import season_sim
        from scoring import norm_name
    except ImportError:
        from models import season_sim
        from models.scoring import norm_name
    import csv as _csv
    snap = snapshot(season)

    board = {}
    try:
        with open(ROOT / "data" / "processed" / "board_2026.csv",
                  newline="", encoding="utf-8") as fh:
            for r in _csv.DictReader(fh):
                try:
                    board[(norm_name(r["name"]), r["pos"])] = float(r["league_pts"])
                except (ValueError, KeyError):
                    continue
    except OSError:
        pass

    rosters, espn_tbl = {}, {}
    all_rostered = []
    for t in snap["teams"]:
        rosters[t["teamName"]] = [{"name": p["name"], "position": p["pos"]}
                                  for p in t["roster"]]
        for p in t["roster"]:
            espn_tbl[(p["name"], p["pos"])] = p["proj"]
            all_rostered.append({**p, "owner": t["teamName"]})

    # our-board lookup, with a scale-consistent fallback for anyone not on the
    # board (the blend runs ~55% of ESPN's scale — mixing raw scales would hand
    # unmatched players a phantom 2x boost)
    matched = [(espn_tbl[(p["name"], p["pos"])], board[(norm_name(p["name"]), p["pos"])])
               for p in all_rostered if (norm_name(p["name"]), p["pos"]) in board
               and espn_tbl[(p["name"], p["pos"])] > 0]
    scale = (sum(b for _, b in matched) / max(1.0, sum(e for e, _ in matched))) if matched else 0.55

    def lookup_espn(name, pos):
        return espn_tbl.get((name, pos), 0.0)

    def lookup_ours(name, pos):
        v = board.get((norm_name(str(name or "")), pos))
        return v if v is not None else lookup_espn(name, pos) * scale

    avail_espn = [{"name": r["name"], "pos": r["pos"], "league_pts": r["proj"]}
                  for r in snap["freeAgents"]]
    avail_ours = [{"name": r["name"], "pos": r["pos"],
                   "league_pts": lookup_ours(r["name"], r["pos"])}
                  for r in snap["freeAgents"]]

    # SEASON STATE (validated 2026-09-15 by replaying 2019-2025 at wks 4/8/12):
    # conditioning the MC on real records + the real remaining schedule beats the
    # old fresh-season sim on every metric (playoff-field hit 75% vs 70%, champ's
    # title% 17% vs 12%, champ rank 3.9 vs 4.8; n=21 year-checkpoints). Honesty
    # note: in that projection-free replay a naive record+PF ranking still edged
    # the MC at raw field-picking (79%) — the sim's job is probabilities and
    # roster-aware forecasts, not ordinal standings.
    st = None
    try:
        lg = _lg(season)
        completed = max(0, int(getattr(lg, "current_week", 1) or 1) - 1)
        recs, obs, name_of = {}, {}, {}
        for t in lg.teams:
            nm = str(getattr(t, "team_name", ""))
            name_of[id(t)] = nm
            recs[nm] = {"wins": int(getattr(t, "wins", 0) or 0),
                        "pf": float(getattr(t, "points_for", 0) or 0)}
            obs[nm] = [float(x or 0) for x in (getattr(t, "scores", None) or [])[:completed]]
        pairs = []
        for wk in range(completed, 14):          # 0-based into the 14-game schedule
            wk_pairs, seen = [], set()
            for t in lg.teams:
                sch = getattr(t, "schedule", None) or []
                if wk < len(sch):
                    a = name_of[id(t)]
                    b = str(getattr(sch[wk], "team_name", ""))
                    key = tuple(sorted((a, b)))
                    if key not in seen:
                        seen.add(key)
                        wk_pairs.append((a, b))
            pairs.append(wk_pairs)
        st = {"week": completed + 1, "records": recs, "observed": obs,
              "schedulePairs": pairs}
    except Exception:
        st = None                                # sim still runs, just stateless

    odds_espn = season_sim.title_odds(rosters, avail_espn, lookup_espn, n_sims=1000,
                                      season_state=st)
    odds_ours = season_sim.title_odds(rosters, avail_ours, lookup_ours, n_sims=1000,
                                      seed=99, season_state=st)

    # TRADE MAP: rostered players the judges rank differently (position-scoped
    # posrank within the rostered pool; positive delta = ESPN likes him LESS)
    def ranks(key):
        out = {}
        for pos in {p["pos"] for p in all_rostered}:
            grp = sorted((p for p in all_rostered if p["pos"] == pos),
                         key=key, reverse=True)
            for i, p in enumerate(grp, 1):
                out[(p["name"], pos)] = i
        return out

    r_espn = ranks(lambda p: p["proj"])
    r_ours = ranks(lambda p: lookup_ours(p["name"], p["pos"]))
    my_team = snap["myTeam"]["teamName"] if snap["myTeam"] else None
    trade_map = []
    for p in all_rostered:
        if p["pos"] in ("K", "DST") or p["proj"] <= 40:
            continue
        d = r_espn[(p["name"], p["pos"])] - r_ours[(p["name"], p["pos"])]
        if abs(d) >= 5:
            trade_map.append({"name": p["name"], "pos": p["pos"], "owner": p["owner"],
                              "mine": p["owner"] == my_team,
                              "espnRank": r_espn[(p["name"], p["pos"])],
                              "ourRank": r_ours[(p["name"], p["pos"])], "delta": d,
                              "read": ("ESPN underrates him — BUY low from an ESPN-brained owner"
                                       if d > 0 else
                                       "ESPN overrates him — SELL high to an ESPN-brained owner")})
    trade_map.sort(key=lambda x: -abs(x["delta"]))

    # The "ours" judge is the PRESEASON draft board — with no weekly reprice it
    # inverts into buy-the-busts/sell-the-breakouts as ESPN updates and it
    # doesn't. Suppress the trade map once real results dominate (wk 5+).
    trade_note = None
    if snap["week"] >= 5:
        trade_map = []
        trade_note = ("trade map suppressed from week 5 — the 'ours' judge is the "
                      "preseason board and its disagreements with ESPN now reflect "
                      "staleness, not edge. Re-enable by repricing the board weekly.")

    out = {"week": snap["week"], "myTeam": my_team,
           "odds": odds_espn, "oddsOurs": odds_ours,
           "fetchedAt": time.strftime("%Y-%m-%d %H:%M"),
           "seasonAware": bool(st),
           "oursJudgeNote": "'ours' = preseason draft board (frozen at draft day)",
           "noiseNote": ("title% carries ~±1pt of Monte-Carlo noise — ranks within a "
                         "tight cluster are ties. Sim is conditioned on real records + "
                         "the real remaining schedule."
                         if st else
                         "title% carries ~±1pt of Monte-Carlo noise — ranks within a "
                         "tight cluster are ties"),
           "tradeMap": trade_map[:12], "tradeMapNote": trade_note}

    # SEASON HISTORY: persist at most one snapshot per day so the standings page
    # can chart every team's title% trajectory across the season. The archive
    # has to start before you need it (the waiver-log lesson).
    try:
        hist = ROOT / "data" / "processed" / "season_history.jsonl"
        today = time.strftime("%Y-%m-%d")
        last = None
        if hist.exists():
            lines = hist.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                last = json.loads(lines[-1]).get("date")
        if last != today:
            with open(hist, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "date": today, "week": snap["week"],
                    "espn": {t: {"title": o["title"], "expWins": o["expWins"]}
                             for t, o in odds_espn.items()},
                    "ours": {t: {"title": o["title"], "expWins": o["expWins"]}
                             for t, o in odds_ours.items()}}) + "\n")
    except OSError:
        pass
    return out


def _board_lookup():
    try:
        from scoring import norm_name
    except ImportError:
        from models.scoring import norm_name
    import csv as _csv
    board = {}
    try:
        with open(ROOT / "data" / "processed" / "board_2026.csv",
                  newline="", encoding="utf-8") as fh:
            for r in _csv.DictReader(fh):
                try:
                    board[(norm_name(r["name"]), r["pos"])] = float(r["league_pts"])
                except (ValueError, KeyError):
                    continue
    except OSError:
        pass
    return board, norm_name


def rosters_report(season: int = 2026) -> dict:
    """Every team's roster valued under BOTH judges + the counterparty dossier:
    draft-era manager profile (titles, tendencies, confidence) and in-season
    activity counts from waiver_log.jsonl. The trades page runs on this."""
    snap = snapshot(season)
    board, norm_name = _board_lookup()
    profiles = {}
    try:
        profiles = json.loads((ROOT / "data" / "processed" / "manager_profiles.json")
                              .read_text(encoding="utf-8"))
    except OSError:
        pass
    activity: dict[str, int] = {}
    try:
        if LOG.exists():
            for ln in LOG.read_text(encoding="utf-8").splitlines():
                try:
                    activity[json.loads(ln).get("team")] = \
                        activity.get(json.loads(ln).get("team"), 0) + 1
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    try:
        import manager_lens as _lens
    except ImportError:
        from models import manager_lens as _lens
    lens_map = _lens.get()
    owner2ui = {}
    try:
        aliases = json.loads((ROOT / "data" / "team_aliases.json").read_text(encoding="utf-8"))
        for ui, owner in aliases.items():
            if not ui.startswith("_") and owner:
                for part in str(owner).split(";"):
                    owner2ui[part.strip().lower()] = ui
    except OSError:
        pass
    teams = []
    for t in snap["teams"]:
        prof = next((profiles[o] for o in t["owners"] if o in profiles), None)
        ui = next((owner2ui.get(o) for o in t["owners"] if o in owner2ui), None)
        lrec = lens_map.get(ui) or {}
        teams.append({
            "teamName": t["teamName"], "owners": t["owners"],
            "lens": lrec.get("lens"), "lensConf": lrec.get("confidence"),
            "lensDetail": (f"{lrec.get('ecrFollows', 0)} expert-side vs "
                           f"{lrec.get('adpFollows', 0)} market-side divergent draft picks"
                           if lrec else None),
            "mine": snap["myTeam"] and t["teamName"] == snap["myTeam"]["teamName"],
            "moves": activity.get(t["teamName"], 0),
            "profile": ({k: prof.get(k) for k in
                         ("titles", "avg_finish", "confidence", "qb_first_round_avg",
                          "rb_in_first4", "wr_in_first4", "summary")} if prof else None),
            "roster": [{**p, "ours": round(board.get((norm_name(p["name"]), p["pos"]),
                                                     0.0), 1)} for p in t["roster"]],
        })
    return {"week": snap["week"], "myTeam": snap["myTeam"]["teamName"],
            "teams": teams}


def trade_eval2(give: list[str], get: list[str], counterparty: str,
                season: int = 2026) -> dict:
    """Evaluate a trade under BOTH judges, BOTH sides. Lineup delta = change in
    best-starting-lineup season points. Negotiation gold: a deal that helps you
    under OUR numbers while helping them under ESPN's (the numbers THEY see) is
    the deal that actually closes."""
    try:
        from inseason import optimal_lineup as _opt, slot_spec as _spec
    except ImportError:
        from models.inseason import optimal_lineup as _opt, slot_spec as _spec
    snap = snapshot(season)
    board, norm_name = _board_lookup()
    spec = _spec(CFG["2026"])
    me = snap["myTeam"]
    them = next((t for t in snap["teams"] if t["teamName"] == counterparty), None)
    if not me or not them:
        return {"error": f"team '{counterparty}' not found"}
    gv, gt = set(give), set(get)

    def val(p, judge):
        if judge == "espn":
            return p["proj"]
        return board.get((norm_name(p["name"]), p["pos"]), p["proj"] * 0.55)

    def lineup(roster, judge):
        return _opt([{"name": p["name"], "pos": p["pos"], "proj": val(p, judge)}
                     for p in roster], spec)["total"]

    out = {"give": sorted(gv), "get": sorted(gt), "with": counterparty}
    for judge in ("espn", "ours"):
        my_after = [p for p in me["roster"] if p["name"] not in gv] \
            + [p for p in them["roster"] if p["name"] in gt]
        their_after = [p for p in them["roster"] if p["name"] not in gt] \
            + [p for p in me["roster"] if p["name"] in gv]
        out[judge] = {
            "myDelta": round(lineup(my_after, judge) - lineup(me["roster"], judge), 1),
            "theirDelta": round(lineup(their_after, judge) - lineup(them["roster"], judge), 1)}
    mine, theirs = out["ours"]["myDelta"], out["espn"]["theirDelta"]
    out["verdict"] = ("PROPOSE IT — helps you by OUR numbers AND looks good to them on ESPN's"
                      if mine >= 3 and theirs >= 0 else
                      "GOOD FOR YOU, hard sell — they lose by their own numbers"
                      if mine >= 3 else
                      "PASS — doesn't move your lineup" if abs(mine) < 3 else
                      "DECLINE — you lose by our numbers")
    return out


def trade_finder(season: int = 2026) -> dict:
    """PROACTIVE trade proposals: packages that help MY lineup by OUR judge
    while looking fair-or-good through the PARTNER'S OWN lens.

    The lens comes from manager_lens.json — fingerprinted from each owner's
    divergent 2026 draft picks (ADP-vs-ECR disagreements: which side did they
    take). Low-n, so it steers the FAIRNESS TEST and the pitch wording, never
    our own valuation: ESPN/market-brained partners are tested on ESPN ROS
    lineup delta (the number their app shows them); FP-expert partners on FP
    rest-of-season consensus ranks (the list they trust). We always keep score
    with OUR board."""
    try:
        from inseason import optimal_lineup as _opt, slot_spec as _spec
        import fp_weekly as _fpw
        import manager_lens as _lens
    except ImportError:
        from models.inseason import optimal_lineup as _opt, slot_spec as _spec
        from models import fp_weekly as _fpw
        from models import manager_lens as _lens
    snap = snapshot(season)
    me = snap["myTeam"]
    if not me:
        return {"error": "no team"}
    board, norm_name = _board_lookup()
    fp = _fpw.ros_get(season)
    lens_map = _lens.get()
    owner2ui = {}
    try:
        aliases = json.loads((ROOT / "data" / "team_aliases.json").read_text(encoding="utf-8"))
        for ui, owner in aliases.items():
            if not ui.startswith("_") and owner:
                for part in str(owner).split(";"):
                    owner2ui[part.strip().lower()] = ui
    except OSError:
        pass
    spec = _spec(CFG["2026"])
    rem = max(1, 19 - snap["week"])

    def ours_val(p):
        return board.get((norm_name(p["name"]), p["pos"]), (p["proj"] or 0) * 0.55)

    def espn_val(p):
        return p["proj"] or 0

    def fp_info(p):
        key = (_fpw.norm(str(p["name"]).replace("D/ST", "").strip().split()[-1])
               if p["pos"] == "DST" else _fpw.norm(p["name"]))
        return fp.get(key)

    def lineup(roster, val):
        return _opt([{"name": q["name"], "pos": q["pos"], "proj": val(q)}
                     for q in roster], spec)["total"]

    my_roster = [p for p in me["roster"] if (p.get("lineupSlot") or "") != "IR"]
    base_mine = lineup(my_roster, ours_val)
    proposals = []
    for t in snap["teams"]:
        if t["teamName"] == me["teamName"]:
            continue
        ui = next((owner2ui.get(o) for o in t["owners"] if o in owner2ui), None)
        lrec = lens_map.get(ui) or {}
        lens = lrec.get("lens", "ESPN/market")
        their = [p for p in t["roster"] if (p.get("lineupSlot") or "") != "IR"]
        base_their_espn = lineup(their, espn_val)
        mine_c = [p for p in my_roster if p["pos"] not in ("K", "DST")]
        their_c = [p for p in their if p["pos"] not in ("K", "DST")]
        combos = [([a], [b]) for a in mine_c for b in their_c]
        tm = sorted(mine_c, key=ours_val, reverse=True)
        tt = sorted(their_c, key=ours_val, reverse=True)[:6]
        for i in range(len(tm)):                     # 2-for-1 consolidations
            for k2 in range(i + 1, min(i + 6, len(tm))):
                for b in tt:
                    combos.append(([tm[i], tm[k2]], [b]))
        for give, get in combos:
            gv = {p["name"] for p in give}
            gt = {p["name"] for p in get}
            my_after = [p for p in my_roster if p["name"] not in gv] + get
            their_after = [p for p in their if p["name"] not in gt] + give
            d_ours = lineup(my_after, ours_val) - base_mine
            if d_ours < 0.35 * rem:                  # must be a real win for us
                continue
            d_their_espn = round(lineup(their_after, espn_val) - base_their_espn, 1)
            in_fp = [fp_info(p) for p in give]       # what THEY receive
            out_fp = [fp_info(p) for p in get]
            # Fairness through THEIR lens — with two hard rules learned from v1:
            # (1) FP rank scales differ per page (QB list vs flex list), so rank
            #     comparisons are only valid SAME-PAGE — a QB17 is not "better"
            #     than a WR3. No same-page anchor -> fall back to the ESPN test.
            # (2) Insult floor: nothing that craters their ESPN lineup by 15+,
            #     whatever the lens says — their app will scream and so will they.
            if lens == "FP-expert" and all(in_fp) and all(out_fp):
                best_out = min(out_fp, key=lambda f: f["avg"])
                same_page = [f for f in in_fp if f["page"] == best_out["page"]]
                if same_page:
                    slack = 5 + 8 * (len(give) - 1)
                    fair = (min(f["avg"] for f in same_page) <= best_out["avg"] + slack
                            and d_their_espn >= -15)
                else:
                    fair = d_their_espn >= -2
                pitch = ("Pitch with FantasyPros: they'd land "
                         + "/".join(f'{p["name"]} ({(fp_info(p) or {}).get("posRank", "?")} ROS)' for p in give)
                         + " for " + "/".join(f'{p["name"]} ({(fp_info(p) or {}).get("posRank", "?")})' for p in get)
                         + " — the better side of FP's rest-of-season list")
            else:
                fair = d_their_espn >= -2
                pitch = (f"Pitch with ESPN: their projected lineup moves {d_their_espn:+} "
                         "ROS pts BY ESPN'S OWN NUMBERS — show them the app")
            if not fair:
                continue
            proposals.append({
                "with": t["teamName"], "lens": lens,
                "lensConf": lrec.get("confidence", "none"),
                "give": [{"name": p["name"], "pos": p["pos"]} for p in give],
                "get": [{"name": p["name"], "pos": p["pos"]} for p in get],
                "myGainOurs": round(d_ours, 1),
                "myGainWk": round(d_ours / rem, 2),
                "theirDeltaEspn": d_their_espn,
                "pitch": pitch})
    proposals.sort(key=lambda x: -x["myGainOurs"])
    top, seen = [], {}
    for pr in proposals:                             # max 2 per partner, 8 total
        if seen.get(pr["with"], 0) >= 2:
            continue
        seen[pr["with"]] = seen.get(pr["with"], 0) + 1
        top.append(pr)
        if len(top) >= 8:
            break
    return {"week": snap["week"], "proposals": top,
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M"),
            "note": ("lens = which board the owner's DRAFT reaches followed (low-n "
                     "fingerprint; steers the pitch, never our valuation). Score is "
                     "kept with OUR board; 'ours' judge is the preseason board — "
                     "weekly reprice still on the roadmap.")}


def planner(season: int = 2026) -> dict:
    """Forward planning: bye-week crunch over the next 4 fantasy weeks (flagging
    the week you go short BEFORE its waiver deadline) + the weeks-15-17 playoff
    panel — each held player's NFL opponent in the only weeks that decide the
    title."""
    try:
        import nfl_schedule
        from inseason import optimal_lineup as _opt, slot_spec as _spec
    except ImportError:
        from models import nfl_schedule
        from models.inseason import optimal_lineup as _opt, slot_spec as _spec
    snap = snapshot(season)
    if not snap["myTeam"]:
        return {"error": "could not identify your team"}
    week = snap["week"]
    sched = nfl_schedule.get(season)
    if not sched:
        return {"error": "NFL schedule unavailable"}
    my = [p for p in snap["myTeam"]["roster"] if (p.get("lineupSlot") or "") != "IR"]
    starters = {p["name"] for _, p in _opt(my, _spec(CFG["2026"]))["starters"]}
    crunch = []
    for wk in range(week, min(week + 4, 15)):
        byes = [p for p in my
                if p.get("team") and nfl_schedule.opponent(sched, p["team"], wk) is None]
        n_start = sum(1 for p in byes if p["name"] in starters)
        crunch.append({"week": wk,
                       "byes": [{"name": p["name"], "pos": p["pos"], "team": p["team"],
                                 "starter": p["name"] in starters} for p in byes],
                       "startersOut": n_start, "alert": n_start >= 3})
    playoff = [{"name": p["name"], "pos": p["pos"], "team": p.get("team"),
                **{f"wk{wk}": (nfl_schedule.opponent(sched, p.get("team"), wk) or "BYE")
                   for wk in (15, 16, 17)}}
               for p in sorted(my, key=lambda r: -(r.get("proj") or 0))]
    return {"week": week, "crunch": crunch, "playoff": playoff,
            "note": ("weeks 15-17 are the fantasy playoffs — opponent-vs-position "
                     "strength attaches once weekly positional data accumulates"),
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M")}


def performance(season: int = 2026) -> dict:
    """Completed-week results: W/L, actual vs projected, hindsight bench leak.
    Empty before week 1 — the page lights up on its own once games exist."""
    try:
        from inseason import optimal_lineup as _opt, slot_spec as _spec, bucket as _b
    except ImportError:
        from models.inseason import optimal_lineup as _opt, slot_spec as _spec, bucket as _b
    lg = _lg(season)
    cur = max(1, int(getattr(lg, "current_week", 1) or 1))
    _, my_owner = _my_ui_and_owner()

    def _owners(t):
        return [str(o.get("displayName") or "").lower()
                for o in (getattr(t, "owners", None) or []) if isinstance(o, dict)]

    weeks = []
    spec = _spec(CFG["2026"])
    for wk in range(1, cur):
        try:
            for m in lg.box_scores(wk):
                for side, opp in (("home", "away"), ("away", "home")):
                    team = getattr(m, f"{side}_team", None)
                    if team is None or (my_owner not in _owners(team)
                                        and "sclsu" not in str(getattr(team, "team_name", "")).lower()):
                        continue
                    mine = getattr(m, f"{side}_lineup", []) or []
                    rows = [{"name": p.name, "pos": _b(getattr(p, "position", "") or ""),
                             "slot": getattr(p, "slot_position", None),
                             "proj": float(getattr(p, "projected_points", 0) or 0),
                             "pts": float(getattr(p, "points", 0) or 0)} for p in mine]
                    started = [r for r in rows if r["slot"] not in ("BE", "IR")]
                    actual = round(sum(r["pts"] for r in started), 1)
                    projected = round(sum(r["proj"] for r in started), 1)
                    # hindsight optimal over players you could LEGALLY have started
                    # (IR-slotted excluded — ESPN won't let them into a lineup)
                    hind = _opt([{"name": r["name"], "pos": r["pos"], "proj": r["pts"]}
                                 for r in rows if r["slot"] != "IR"], spec)["total"]
                    opp_score = round(float(getattr(m, f"{opp}_score", 0) or 0), 1)
                    weeks.append({"week": wk, "actual": actual, "projected": projected,
                                  "optimalHindsight": round(hind, 1),
                                  "benchLeak": round(hind - actual, 1),
                                  "opponent": getattr(getattr(m, f"{opp}_team", None),
                                                      "team_name", None),
                                  "oppScore": opp_score,
                                  "result": "W" if actual > opp_score else
                                            ("L" if actual < opp_score else "T")})
        except Exception:
            continue
    standings = [{"team": getattr(t, "team_name", None),
                  "wins": getattr(t, "wins", 0), "losses": getattr(t, "losses", 0),
                  "pf": round(float(getattr(t, "points_for", 0) or 0), 1)}
                 for t in lg.teams]
    # ALL-PLAY record + luck: your weekly score vs ALL teams — the standings with
    # schedule luck stripped out. Measured 2019-25: 26% of playoff spots here go
    # to teams outside the all-play top 6; luck swings ±1.5 wins/season (sd).
    try:
        scores_by_team = {str(getattr(t, "team_name", "")):
                          [float(x or 0) for x in (getattr(t, "scores", None) or [])[:cur - 1]]
                          for t in lg.teams}
        n_wk = max((len(v) for v in scores_by_team.values()), default=0)
        apw = {nm: 0.0 for nm in scores_by_team}
        for wk in range(n_wk):
            wk_scores = {nm: v[wk] for nm, v in scores_by_team.items() if len(v) > wk and v[wk] > 0}
            for nm, sc in wk_scores.items():
                others = [s for o, s in wk_scores.items() if o != nm]
                if others:
                    apw[nm] += (sum(1 for s in others if s < sc)
                                + 0.5 * sum(1 for s in others if s == sc)) / len(others)
        for r in standings:
            ap = apw.get(str(r["team"]), 0.0)
            r["allPlayWins"] = round(ap, 1)          # expected wins on pure scoring
            r["luck"] = round(r["wins"] - ap, 1)     # + = schedule-gifted, − = robbed
    except Exception:
        pass
    standings.sort(key=lambda r: (-r["wins"], -r["pf"]))
    # PF rank shown alongside the wins-sorted table — a wins-only sort once
    # hid a 9th-in-points reality behind a 5th-looking row
    by_pf = sorted(standings, key=lambda r: -r["pf"])
    for r in standings:
        r["pfRank"] = next(i for i, x in enumerate(by_pf, 1) if x["team"] == r["team"])
    return {"currentWeek": cur, "weeks": weeks, "standings": standings,
            "fetchedAt": time.strftime("%Y-%m-%d %H:%M")}


if __name__ == "__main__":
    rep = report()
    if rep.get("error"):
        raise SystemExit("ERROR: " + rep["error"])
    print(f"=== WAIVER WIRE · {rep['myTeam']} · week {rep['week']} "
          f"· lineup ROS proj {rep['myLineupProj']} ===\n")
    if rep["allClear"]:
        print("✓ ALL CLEAR — nothing on the wire needs action. Best move: no move.")
    for a in rep["actions"]:
        d = a.get("drop")
        print(f"▶ {a['type']}: {a['add']['name']} ({a['add']['pos']} {a['add']['proj']})"
              + (f"  — drop {d['name']} ({d['pos']} {d['proj']})" if d else "")
              + f"  NET {'+' if a['netVorp'] >= 0 else ''}{a['netVorp']} VORP")
        print(f"   {a['why']}")
    print(f"\n{rep['stream']['line']}")
    if rep["watchlist"]:
        print("\nWATCHLIST (same-slot dart swaps, no urgency):")
        for m in rep["watchlist"]:
            print(f"  {m['add']['name']} ({m['add']['pos']}) over {m['drop']['name']}"
                  f" — net +{m['netVorp']}")
    if rep["injuryFlags"]:
        print("\nINJURY NOTES (auto-fetched):")
        for f in rep["injuryFlags"]:
            print(f"  ⚕ {f['name']} ({f['pos']}) {f['injury']}"
                  + (f" [{f.get('newsDate')}]" if f.get("newsDate") else ""))
            if f.get("news"):
                print(f"     {f['news'][:200]}")
    if rep["roomActivity"]:
        print("\nROOM ACTIVITY (logged to waiver_log.jsonl):")
        for m in rep["roomActivity"][:8]:
            print(f"  {str(m['team'])[:20]:<20} {m['action']:<12} {m['player']}"
                  + (f"  (${m['bid']})" if m.get("bid") else ""))
