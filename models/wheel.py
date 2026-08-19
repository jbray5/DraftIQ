"""THE WHEEL planner — two-pick strategy for slot 10's back-to-back turns.

Drafting at the turn is a different game: you make TWO picks at once, then wait
18 picks while the room strip-mines the board. The two decisions that matter:

  1. WHICH PAIR now — not the top two names on a one-pick list. Taking an RB
     changes what your second pick should be, so we simulate: for each top
     candidate A, re-run the live pick engine with A on the roster to find its
     true best partner B, and rank the resulting pairs by combined value.

  2. WHAT DIES in the 18-pick wait — answered honestly by playing the room
     forward with the opponent model (manager priors, needs, runs) N times and
     counting survivors, instead of a closed-form ADP guess. Output: per-player
     survival odds for the names you care about + expected best-at-position
     when the board comes back to you.

Deterministic per seed set. No LLM. Served at /api/wheel-plan.
"""
from __future__ import annotations

try:
    import opponent_ai as oa
    import pick_engine as pe
except ImportError:  # pragma: no cover
    from models import opponent_ai as oa
    from models import pick_engine as pe

N_SIMS = 25          # room simulations for the survival forecast
TOP_A = 5            # first-pick candidates to branch on
SLOT_ORDER = {"RB": ["RB1", "RB2", "FLEX", "FLEX2"], "WR": ["WR1", "WR2", "FLEX", "FLEX2"],
              "TE": ["TE", "FLEX", "FLEX2"]}


def _place(my_roster: dict, player: dict) -> dict:
    """Roster dict with `player` slotted like the live UI would slot them."""
    out = dict(my_roster or {})
    pos = player["position"]
    for s in SLOT_ORDER.get(pos, [pos]):
        if not out.get(s):
            out[s] = {"name": player["name"], "position": pos}
            return out
    return out          # bench — roster dict unchanged (bench is sent separately)


def is_my_turn_pair(overall: int, my_slot: int, teams: int) -> bool:
    return (oa.slot_for_pick(overall, teams) == my_slot
            and overall + 1 <= 10 ** 9
            and oa.slot_for_pick(overall + 1, teams) == my_slot)


def plan(board_rows, drafted, my_roster, picks_log, overall_pick, teams=10,
         team_order=None, team_rosters=None, my_slot=None, rounds=18, bench=None,
         profiles=None, alias_map=None) -> dict:
    """The full wheel read for the CURRENT pick. Cheap enough to run every turn."""
    args = dict(teams=teams, team_order=team_order, team_rosters=team_rosters,
                my_slot=my_slot, rounds=rounds, bench=bench,
                profiles=profiles, alias_map=alias_map)
    base = pe.shortlist(board_rows, set(drafted), my_roster, picks_log, overall_pick, **args)
    sl = base["shortlist"]
    pair_turn = bool(my_slot) and is_my_turn_pair(overall_pick, my_slot, teams)

    # ---- 1. pair search: branch on each top first-pick, find its best partner ----
    pairs = []
    if pair_turn and sl:
        seen_pos_pairs = set()
        for a in sl[:TOP_A]:
            if (a.get("pickNowValue") or 0) <= 0:
                continue
            roster_a = _place(my_roster, a)
            drafted_a = set(drafted) | {a["name"]}
            log_a = list(picks_log or []) + [{"overall": overall_pick, "team": "ME",
                                              "name": a["name"], "pos": a["position"]}]
            nxt = pe.shortlist(board_rows, drafted_a, roster_a, log_a,
                               overall_pick + 1, **args)
            if not nxt["shortlist"]:
                continue
            b = nxt["shortlist"][0]
            key = tuple(sorted([a["position"], b["position"]]))
            combined = round((a.get("pickNowValue") or 0) + (b.get("pickNowValue") or 0), 4)
            entry = {
                "first": {k: a.get(k) for k in ("name", "position", "team", "proj", "vorp",
                                                "adp", "tier", "urgency", "fits")},
                "second": {k: b.get(k) for k in ("name", "position", "team", "proj", "vorp",
                                                 "adp", "tier", "urgency", "fits")},
                "combined": combined,
                "posPair": f"{a['position']}+{b['position']}",
            }
            if key in seen_pos_pairs:       # keep only the best pair per position shape
                worse = next((p for p in pairs
                              if tuple(sorted(p["posPair"].split("+"))) == key
                              and p["combined"] < combined), None)
                if worse:
                    pairs.remove(worse)
                    pairs.append(entry)
                continue
            seen_pos_pairs.add(key)
            pairs.append(entry)
        pairs.sort(key=lambda p: -p["combined"])
        pairs = pairs[:3]

    # ---- 2. survival forecast: play the room forward N times ----
    start = overall_pick + (2 if pair_turn else 1)      # after my pick(s)
    watch = [c for c in sl if (c.get("pickNowValue") or 0) > 0][:12]
    survive = {c["name"]: 0 for c in watch}
    best_at_next: dict[str, list[float]] = {}
    next_pick = None
    took_pair = pairs[0] if pairs else None
    for i in range(N_SIMS):
        log = list(picks_log or [])
        gone = set(drafted)
        if pair_turn and took_pair:          # assume the recommended pair leaves with me
            for c in (took_pair["first"], took_pair["second"]):
                gone.add(c["name"])
                log.append({"overall": overall_pick, "team": "ME",
                            "name": c["name"], "pos": c["position"]})
        res = oa.simulate(board_rows, log, team_order or [], start, my_slot or 0,
                          teams=teams, rounds=rounds, profiles=profiles,
                          alias_map=alias_map, seed=1000 + i * 17)
        next_pick = res["nextPick"]
        taken = {p["name"] for p in res["picks"]} | gone
        for nm in survive:
            if nm not in taken:
                survive[nm] += 1
        # best remaining at each position after the room has fed
        rem: dict[str, float] = {}
        for r in board_rows:
            if r["name"] in taken:
                continue
            pos = pe.normalize_pos(r.get("pos") or r.get("position"))
            try:
                pts = float(r.get("league_pts") or 0)
            except (TypeError, ValueError):
                pts = 0.0
            if pts > rem.get(pos, 0.0):
                rem[pos] = pts
        for pos, pts in rem.items():
            best_at_next.setdefault(pos, []).append(pts)

    forecast = [{"name": c["name"], "pos": c["position"],
                 "surviveProb": round(survive[c["name"]] / N_SIMS, 2),
                 "proj": c.get("proj"), "vorp": c.get("vorp")}
                for c in watch
                if not (took_pair and c["name"] in
                        (took_pair["first"]["name"], took_pair["second"]["name"]))]
    forecast.sort(key=lambda x: x["surviveProb"])

    per_pos = []
    for pos in ("RB", "WR", "TE", "QB", "IDP"):
        vals = best_at_next.get(pos)
        if vals:
            per_pos.append({"pos": pos, "expBestProj": round(sum(vals) / len(vals))})
    now_best = {c["position"]: c.get("proj") for c in sl if c.get("proj")}
    for e in per_pos:
        if e["pos"] in now_best:
            e["costOfWaiting"] = round((now_best[e["pos"]] or 0) - e["expBestProj"])

    return {"isPairTurn": pair_turn, "pairs": pairs, "forecast": forecast,
            "expectedAtNextTurn": per_pos, "nextPick": next_pick,
            "simCount": N_SIMS, "anchor": base["anchor"]}


if __name__ == "__main__":     # python models/wheel.py — self-test at pick 10 (the wheel)
    import csv
    import json
    import time
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    rows = list(csv.DictReader(open(ROOT / "data/processed/board_2026.csv", encoding="utf-8")))
    profiles = json.loads((ROOT / "data/processed/manager_profiles.json").read_text(encoding="utf-8"))
    aliases = {k: v for k, v in json.loads((ROOT / "data/team_aliases.json").read_text(
        encoding="utf-8")).items() if not k.startswith("_")}
    order = ["Gilbert", "Bollinger", "Hubauer", "Putman", "Walker", "Wester", "Spivey", "Street", "Munford", "Ray"]
    gone = ["Bijan Robinson", "Jahmyr Gibbs", "Puka Nacua", "Christian McCaffrey",
            "Ja'Marr Chase", "Jonathan Taylor", "De'Von Achane", "Amon-Ra St. Brown",
            "Jaxon Smith-Njigba"]
    log = [{"overall": i + 1, "team": order[i % 9], "name": n,
            "pos": "RB" if i % 2 else "WR"} for i, n in enumerate(gone)]

    t0 = time.time()
    res = plan(rows, gone, {}, log, 10, teams=10, team_order=order, my_slot=10,
               rounds=18, profiles=profiles, alias_map=aliases)
    dt = time.time() - t0
    print(f"wheel plan at pick 10 ({dt:.1f}s, {res['simCount']} room sims):")
    print(f"  pair turn: {res['isPairTurn']}   next pick after wait: {res['nextPick']}")
    for i, p in enumerate(res["pairs"], 1):
        print(f"  PAIR {i}: {p['first']['name']} ({p['first']['position']}) + "
              f"{p['second']['name']} ({p['second']['position']})  [{p['posPair']}] "
              f"combined={p['combined']}")
    print("\n  survival forecast (18-pick wait, room simulated):")
    for f in res["forecast"][:8]:
        print(f"    {f['name']:<24} {f['pos']:<4} survives {f['surviveProb']:.0%}")
    print("\n  expected best-at-position when it wheels back:")
    for e in res["expectedAtNextTurn"]:
        cost = f"  (cost of waiting ~{e.get('costOfWaiting')} pts)" if "costOfWaiting" in e else ""
        print(f"    {e['pos']:<4} ~{e['expBestProj']} proj{cost}")
