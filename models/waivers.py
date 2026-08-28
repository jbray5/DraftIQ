"""In-season WAIVER WIRE engine — decision-grade, not homework.

The league's own history says the only REPEATABLE owner skills are in-season
(streaming volume + waiver production). This module turns that into a report
whose contract is: ACTIONS are things you should actually do — everything else
is context. If there is nothing to do, it says ALL CLEAR and means it.

  * ACTIONS: injury-driven claims (the engine pulls each flagged player's real
    injury note from ESPN's athlete API and prices the replacement move),
    lineup-cracking upgrades, and D/ST stream switches. Each carries the full
    move: add → drop → netVorp, plus the WHY with the fetched news line.
  * WATCHLIST: same-position bench upgrades with a real job (dart swaps at
    positions you actually roster thin). Backup-QB hoarding never shows.
  * Stream verdict collapses to one line when it's HOLD.
  * ROOM ACTIVITY: every pull appends to data/processed/waiver_log.jsonl so
    opponent add/drop tendencies accumulate for later analysis + trade intel.

Everything is valued on ESPN's rest-of-season projections — league-scored
(appliedTotal), so skill/IDP/K/DST share one honest scale. In-season
replacement = the 5th-best free agent at the position (the wire itself).
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
BAD_STATUS = {"OUT", "INJURY_RESERVE", "IR", "SUSPENSION", "PHYSICALLY_UNABLE_TO_PERFORM"}
FA_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST", "LB", "DE", "DT", "CB", "S")
ATH = "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{pid}"


def _row(p) -> dict:
    """espn_api Player -> plain dict on the league-scored ROS scale."""
    return {
        "playerId": getattr(p, "playerId", None),
        "name": getattr(p, "name", None),
        "pos": bucket(getattr(p, "position", "") or ""),
        "espnPos": getattr(p, "position", None),
        "team": getattr(p, "proTeam", None),
        "proj": round(float(getattr(p, "projected_total_points", 0) or 0), 1),
        "injury": (str(getattr(p, "injuryStatus", "") or "").upper() or None),
    }


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
    lg = _league(season)
    ui_name, my_owner = _my_ui_and_owner()
    my_team, teams = None, []
    for t in lg.teams:
        owners = [str(o.get("displayName") or "").lower()
                  for o in (getattr(t, "owners", None) or []) if isinstance(o, dict)]
        roster = [_row(p) for p in (getattr(t, "roster", None) or [])]
        rec = {"teamName": getattr(t, "team_name", None), "owners": owners, "roster": roster}
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
    week = max(1, int(getattr(lg, "current_week", 1) or 1))
    return {"week": week, "myTeam": my_team, "teams": teams, "freeAgents": fas}


def _log_activity(moves: list[dict]) -> None:
    """Accumulate the room's moves for tendency analysis + trade intel."""
    try:
        seen = set()
        if LOG.exists():
            for line in LOG.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                    seen.add((d.get("team"), d.get("action"), d.get("player")))
                except json.JSONDecodeError:
                    continue
        with open(LOG, "a", encoding="utf-8") as fh:
            for m in moves:
                key = (m.get("team"), m.get("action"), m.get("player"))
                if key not in seen:
                    fh.write(json.dumps({**m, "loggedAt": time.strftime("%Y-%m-%d")}) + "\n")
                    seen.add(key)
    except OSError:
        pass


def report(season: int = 2026, week: int | None = None) -> dict:
    snap = snapshot(season)
    if not snap["myTeam"]:
        return {"error": "could not identify your team in the league"}
    week = week or snap["week"]
    my = snap["myTeam"]["roster"]
    spec = slot_spec(CFG["2026"])
    fas = snap["freeAgents"]

    # in-season replacement = 5th-best FA at the position (the wire next week)
    by_pos: dict[str, list[float]] = {}
    for r in fas:
        by_pos.setdefault(r["pos"], []).append(r["proj"])
    repl = {pos: (lst[4] if len(lst) > 4 else lst[-1]) for pos, lst in by_pos.items()}

    lineup = optimal_lineup(my, spec)

    def _vorp(p) -> float:
        return round((p["proj"] or 0) - repl.get(p["pos"], 0), 1)

    def _best_drop(add_row, protect: set[str] = frozenset()):
        after = optimal_lineup(my + [add_row], spec)
        keep = {p["name"] for _, p in after["starters"]} | set(protect)
        bench = [p for p in my if p["name"] not in keep]
        return min(bench, key=_vorp) if bench else None

    def _move(add, protect: set[str] = frozenset()):
        drop = _best_drop({k: add[k] for k in ("name", "pos", "proj")}, protect)
        net = round(_vorp(add) - (_vorp(drop) if drop else 0), 1)
        return {"add": {k: add[k] for k in ("name", "pos", "team", "proj")},
                "drop": ({**{k: drop[k] for k in ("name", "pos", "team", "proj")},
                          "vsWire": _vorp(drop)} if drop else None),
                "vsWire": _vorp(add), "netVorp": net}

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
        without = [({**q, "proj": 0.0} if q["name"] == p["name"] else q) for q in my]
        cands = [f for f in fas if f["pos"] == p["pos"]][:8]
        best, best_gain = None, 0.0
        base = optimal_lineup(without, spec)["total"]
        for f in cands:
            gain = optimal_lineup(without + [f], spec)["total"] - base
            if gain > best_gain:
                best, best_gain = f, gain
        if best and best_gain > 1 and best["name"] not in claimed:
            mv = _move(best, protect={p["name"]})   # never drop the injured guy blind
            claimed.add(best["name"])
            actions.append({
                "type": "CLAIM", "urgency": "act before waivers clear",
                "why": (f"{p['name']} is {p['injury']}"
                        + (f" — {news['note']}" if news.get("note") else "")
                        + f". {best['name']} is the best wire {p['pos']} and starts while he's out."),
                **mv})

    # ---- 2. straight lineup-crackers (healthy roster, FA is just better) ----
    targets = waiver_targets(my, fas[:120], repl, spec, top=25)
    for t in targets:
        if t["lineup_gain"] > 1 and t["name"] not in claimed:
            mv = _move(t)
            claimed.add(t["name"])
            actions.append({"type": "CLAIM", "urgency": "clear upgrade",
                            "why": (f"{t['name']} beats your current starter — "
                                    f"+{t['lineup_gain']} lineup pts before any injury help."),
                            **mv})

    # ---- 3. D/ST stream switch ----
    stream: dict = {"week": week}
    try:
        ranks = week1_odds.dst_ranks(season, week)

        def _tc(r):
            return str(r.get("team") or "").upper()

        my_dsts = [p for p in my if p["pos"] == "DST"]
        mine = [{"name": p["name"], **(ranks.get(_tc(p)) or {})} for p in my_dsts]
        fa_dsts = sorted(({"name": r["name"], "pos": "DST", "team": r["team"],
                           "proj": r["proj"], **(ranks.get(_tc(r)) or {})}
                          for r in fas if r["pos"] == "DST"),
                         key=lambda x: x.get("w1Rank") or 99)
        my_best = min((m.get("w1Rank") or 99) for m in mine) if mine else 99
        fa_best = fa_dsts[0] if fa_dsts else None
        stream.update({"myDst": mine, "hold": True,
                       "line": f"D/ST: HOLD — yours has the better matchup (#{my_best})"})
        if fa_best and (fa_best.get("w1Rank") or 99) + 2 < my_best:
            stream["hold"] = False
            stream["line"] = (f"STREAM {fa_best['name']} (matchup #{fa_best['w1Rank']}) "
                              f"over yours (#{my_best})")
            actions.append({"type": "STREAM", "urgency": f"before week {week} locks",
                            "why": (f"Implied-total matchup #{fa_best['w1Rank']} vs your #{my_best} "
                                    "— the validated +55 pts/season play."),
                            **_move(fa_best)})
    except Exception as e:
        stream["line"] = f"stream check unavailable ({e})"

    # ---- WATCHLIST: same-position dart swaps with a real job, nothing else ----
    my_pos_count: dict[str, int] = {}
    for p in my:
        my_pos_count[p["pos"]] = my_pos_count.get(p["pos"], 0) + 1
    watch = []
    for t in targets:
        if t["name"] in claimed or t["lineup_gain"] > 1:
            continue
        pos = t["pos"]
        if pos in ("K", "DST", "IDP"):
            continue                      # streamers/hold — never hoard on the bench
        if pos == "QB" and my_pos_count.get("QB", 0) >= 2:
            continue                      # no backup-QB hoarding in a 10-team league
        if pos == "TE" and my_pos_count.get("TE", 0) >= 2:
            continue
        mv = _move(t)
        if mv["netVorp"] >= 12 and mv["drop"] and mv["drop"]["pos"] == pos:
            watch.append({**mv, "why": f"straight {pos} dart upgrade — same slot, +{mv['netVorp']} value"})
        if len(watch) >= 5:
            break

    # ---- room activity (display + persistent log for tendency analysis) ----
    moves = []
    try:
        lg = _league(season)
        for act in lg.recent_activity(size=25):
            for team, action, player, bid in getattr(act, "actions", []):
                moves.append({"team": getattr(team, "team_name", None),
                              "action": action,
                              "player": getattr(player, "name", str(player)),
                              "bid": bid})
    except Exception:
        pass
    _log_activity(moves)

    return {"week": week, "myTeam": snap["myTeam"]["teamName"],
            "myLineupProj": lineup["total"], "allClear": not actions,
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
    lg = _league(season)
    week = week or max(1, int(getattr(lg, "current_week", 1) or 1))
    _, my_owner = _my_ui_and_owner()

    def _owners(t):
        return [str(o.get("displayName") or "").lower()
                for o in (getattr(t, "owners", None) or []) if isinstance(o, dict)]

    def _lineup_rows(lineup):
        return [{"name": p.name, "pos": _b(getattr(p, "position", "") or ""),
                 "slot": getattr(p, "slot_position", None),
                 "proj": round(float(getattr(p, "projected_points", 0) or 0), 1),
                 "injury": (str(getattr(p, "injuryStatus", "") or "").upper() or None)}
                for p in (lineup or [])]

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
            opt = _opt(mine, spec)
            opt_names = {p["name"] for _, p in opt["starters"]}
            cur_names = {p["name"] for p in started}
            swaps = [{"start": n} for n in sorted(opt_names - cur_names)] and \
                    [{"start": i, "sit": o} for i, o in
                     zip(sorted(opt_names - cur_names), sorted(cur_names - opt_names))]
            opp_total = round(sum(p["proj"] for p in theirs if p["slot"] not in ("BE", "IR")), 1)
            sd = season_sim.weekly_sd()
            win = 0.5 * (1 + math.erf((opt["total"] - opp_total) / (sd * math.sqrt(2) * math.sqrt(2))))
            return {"week": week,
                    "me": {"team": getattr(team, "team_name", None), "current": started,
                           "bench": [p for p in mine if p["slot"] in ("BE", "IR")],
                           "currentTotal": my_total, "optimalTotal": opt["total"],
                           "optimalNames": sorted(opt_names)},
                    "swaps": swaps, "benchLeak": round(opt["total"] - my_total, 1),
                    "opponent": {"team": getattr(opp_team, "team_name", None),
                                 "projTotal": opp_total,
                                 "lineup": [p for p in theirs if p["slot"] not in ("BE", "IR")],
                                 "bench": [p for p in theirs if p["slot"] in ("BE", "IR")]},
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
    odds_espn = season_sim.title_odds(rosters, avail_espn, lookup_espn, n_sims=1000)
    odds_ours = season_sim.title_odds(rosters, avail_ours, lookup_ours, n_sims=1000, seed=99)

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

    out = {"week": snap["week"], "myTeam": my_team,
           "odds": odds_espn, "oddsOurs": odds_ours,
           "noiseNote": "title% carries ~±1pt of Monte-Carlo noise — ranks within a tight cluster are ties",
           "tradeMap": trade_map[:12]}

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
