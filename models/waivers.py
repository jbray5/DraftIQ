"""In-season WAIVER WIRE engine — the validated post-draft edge, operational.

The league's own history says the only REPEATABLE owner skills are in-season:
streaming volume and waiver production (draft outcomes don't repeat year-to-year).
This module turns that finding into a weekly report:

  * LIVE rosters + free agents from ESPN (same auth as espn_proj), valued by
    ESPN's rest-of-season projections — already scored in THIS league's exact
    rules (appliedTotal), so skill/IDP/K/DST are all on one honest scale.
  * UPGRADES: free agents ranked by "does he crack my optimal lineup" (the
    inseason.waiver_targets math) with a concrete drop suggestion each.
  * STREAMS: the D/ST play for the coming week by implied opponent total
    (validated −0.294, +55 pts/season) via week1_odds' week-parameterized feed;
    K shown as context only (r=+0.114 — never chase kickers).
  * INJURY FLAGS on my roster (OUT/IR/suspension) and the league's recent
    add/drop activity so you see the room moving.

Replacement level in-season = the best free agent at the position (the literal
"free temp"): a rostered player is only worth what he clears over the wire.

Run `python models/waivers.py` for the CLI report; served at /api/waivers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

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
BAD_STATUS = {"OUT", "INJURY_RESERVE", "IR", "SUSPENSION", "DOUBTFUL"}
FA_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST", "LB", "DE", "DT", "CB", "S")


def _row(p) -> dict:
    """espn_api Player -> plain dict on the league-scored ROS scale."""
    return {
        "name": getattr(p, "name", None),
        "pos": bucket(getattr(p, "position", "") or ""),
        "espnPos": getattr(p, "position", None),
        "team": getattr(p, "proTeam", None),
        "proj": round(float(getattr(p, "projected_total_points", 0) or 0), 1),
        "injury": (str(getattr(p, "injuryStatus", "") or "").upper() or None),
    }


def _my_ui_and_owner() -> tuple[str, str]:
    """('JRay', 'jbray5') from team_aliases.json — the user's UI name + handle."""
    aliases = json.loads((ROOT / "data" / "team_aliases.json").read_text(encoding="utf-8"))
    for ui, owner in aliases.items():
        if ui.lower() == "jray":
            return ui, str(owner).lower()
    return "JRay", "jbray5"


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
                # trust the player's OWN position (two-way-player guard, as espn_proj)
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


def report(season: int = 2026, week: int | None = None) -> dict:
    snap = snapshot(season)
    if not snap["myTeam"]:
        return {"error": "could not identify your team in the league"}
    week = week or snap["week"]
    my = snap["myTeam"]["roster"]
    spec = slot_spec(CFG["2026"])
    fas = snap["freeAgents"]

    # in-season replacement = the 5TH-best free agent at the position: the "free
    # temp" you could still get NEXT week after a few claims clear. (Using the
    # single best FA zeroes every vsWire value by construction — the top FA is
    # his own replacement — which silently blanked the depth-adds section.)
    by_pos: dict[str, list[float]] = {}
    for r in fas:
        by_pos.setdefault(r["pos"], []).append(r["proj"])
    repl = {pos: (lst[4] if len(lst) > 4 else lst[-1]) for pos, lst in by_pos.items()}

    # ---- UPGRADES + DEPTH ADDS, each priced as a full MOVE ----
    # The currency is in-season VORP: value over the wire. A move's net value is
    #   netVorp = (add.proj − wire[add.pos]) − (drop.proj − wire[drop.pos])
    # i.e. what your roster gains in value-over-replacement after BOTH halves of
    # the transaction. lineupGain is the separate, immediate starter-points bump.
    lineup = optimal_lineup(my, spec)

    def _vorp(p) -> float:
        return round((p["proj"] or 0) - repl.get(p["pos"], 0), 1)

    def _best_drop(add_row):
        """Cheapest cut: the bench player (never a current starter — protects the
        single K/DST/IDP) worth the least over the wire once the add is rostered."""
        after = optimal_lineup(my + [add_row], spec)
        keep = {p["name"] for _, p in after["starters"]}
        bench = [p for p in my if p["name"] not in keep]
        return min(bench, key=_vorp) if bench else None

    def _move(t):
        add = {k: t[k] for k in ("name", "pos", "team", "proj")}
        drop = _best_drop({k: t[k] for k in ("name", "pos", "proj")})
        net = round(_vorp(add) - (_vorp(drop) if drop else 0), 1)
        return {"add": add, "lineupGain": t["lineup_gain"], "vsWire": t["vorp"],
                "drop": ({**{k: drop[k] for k in ("name", "pos", "team", "proj")},
                          "vsWire": _vorp(drop)} if drop else None),
                "netVorp": net}

    targets = waiver_targets(my, fas[:120], repl, spec, top=25)
    upgrades = [_move(t) for t in targets if t["lineup_gain"] > 0.1][:8]
    depth = sorted((_move(t) for t in targets
                    if t["lineup_gain"] <= 0.1 and t["vorp"] > 0),
                   key=lambda m: -m["netVorp"])[:6]
    depth = [m for m in depth if m["netVorp"] > 0]

    # ---- STREAMS: this week's D/ST by implied opponent total ----
    stream: dict = {"week": week}
    try:
        ranks = week1_odds.dst_ranks(season, week)
        my_dsts = [p for p in my if p["pos"] == "DST"]
        fa_dsts = [r for r in fas if r["pos"] == "DST"]

        def _mark(nm):
            key = next((k for k in ranks if k and nm and k in str(nm).upper()), None)
            return ranks.get(key) if key else None

        def _team_code(r):   # espn D/ST rows: proTeam carries the code
            return str(r.get("team") or "").upper()

        mine = [{"name": p["name"], **(ranks.get(_team_code(p)) or {})} for p in my_dsts]
        best_fa = sorted(
            ({"name": r["name"], "proj": r["proj"], **(ranks.get(_team_code(r)) or {})}
             for r in fa_dsts),
            key=lambda x: x.get("w1Rank") or 99)[:5]
        stream["myDst"] = mine
        stream["bestAvailable"] = best_fa
        my_best = min((m.get("w1Rank") or 99) for m in mine) if mine else 99
        fa_best = best_fa[0].get("w1Rank") if best_fa else None
        stream["verdict"] = (
            f"STREAM: {best_fa[0]['name']} (matchup #{fa_best}) over your current unit (#{my_best})"
            if fa_best and fa_best + 2 < my_best else
            "HOLD: your defense has the better matchup this week")
    except Exception as e:
        stream["error"] = str(e)

    # ---- INJURY FLAGS on my roster ----
    flags = [{k: p[k] for k in ("name", "pos", "team", "proj", "injury")}
             for p in my if p.get("injury") in BAD_STATUS]

    # ---- ROOM ACTIVITY: recent adds/drops league-wide ----
    moves = []
    try:
        lg = _league(season)
        for act in lg.recent_activity(size=20):
            for team, action, player, bid in getattr(act, "actions", []):
                moves.append({"team": getattr(team, "team_name", None),
                              "action": action,
                              "player": getattr(player, "name", str(player)),
                              "bid": bid})
    except Exception:
        pass

    return {"week": week, "myTeam": snap["myTeam"]["teamName"],
            "myLineupProj": lineup["total"],
            "upgrades": upgrades, "depthAdds": depth, "stream": stream,
            "injuryFlags": flags, "roomActivity": moves[:20]}


if __name__ == "__main__":
    rep = report()
    if rep.get("error"):
        raise SystemExit("ERROR: " + rep["error"])
    print(f"=== WAIVER WIRE · {rep['myTeam']} · week {rep['week']} "
          f"· lineup proj {rep['myLineupProj']} ===")
    print("\nUPGRADES (crack your starting lineup):")
    if not rep["upgrades"]:
        print("  none — the wire has nothing that beats your starters")
    for u in rep["upgrades"]:
        d = u["drop"]
        print(f"  + {u['add']['name']:<24}{u['add']['pos']:<4} lineup +{u['lineupGain']:<6}"
              + (f" drop {d['name']} ({d['pos']})" if d else "")
              + f"  NET {'+' if u['netVorp'] >= 0 else ''}{u['netVorp']} VORP")
    print("\nDEPTH ADDS (beat the wire, don't start yet):")
    for m in rep["depthAdds"]:
        d = m["drop"]
        print(f"  + {m['add']['name']:<24}{m['add']['pos']:<4} proj {m['add']['proj']:>6}"
              + (f"  drop {d['name']} ({d['pos']} {d['proj']})" if d else "")
              + f"  NET +{m['netVorp']} VORP")
    st = rep["stream"]
    print(f"\nD/ST STREAM (week {st.get('week')}):  {st.get('verdict', st.get('error'))}")
    for m in (st.get("myDst") or []):
        print(f"  mine: {m.get('name')} — matchup #{m.get('w1Rank')} vs {m.get('opp')}")
    for f in (st.get("bestAvailable") or [])[:3]:
        print(f"  wire: {f.get('name')} — matchup #{f.get('w1Rank')} vs {f.get('opp')}")
    if rep["injuryFlags"]:
        print("\nINJURY FLAGS on your roster:")
        for f in rep["injuryFlags"]:
            print(f"  ⚕ {f['name']} ({f['pos']}) — {f['injury']}")
    if rep["roomActivity"]:
        print("\nROOM ACTIVITY (recent):")
        for m in rep["roomActivity"][:10]:
            print(f"  {str(m['team'])[:20]:<20} {m['action']:<12} {m['player']}"
                  + (f"  (${m['bid']})" if m.get("bid") else ""))
