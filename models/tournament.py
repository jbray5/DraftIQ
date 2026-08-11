"""WAR GAMES — play the draft out N times and see what actually wins.

From the CURRENT draft state (works pre-draft or mid-draft): each simulation
finishes the draft — your picks driven by the live pick engine's anchor, the
other nine by the opponent model with their real tendencies — then the finished
league is Monte-Carlo'd for title odds. Across N runs you get:

  * your title-odds distribution (how good/bad this draft can go)
  * which early POSITION SEQUENCES produced your best seasons
  * your most-drafted players ("the model keeps buying these")
  * the single best draft found, in full

Deterministic per seed set; no LLM. Served at /api/sim/tournament.
"""
from __future__ import annotations

import random
from collections import Counter

try:
    import opponent_ai as oa
    import pick_engine as pe
    import season_sim
except ImportError:  # pragma: no cover
    from models import opponent_ai as oa
    from models import pick_engine as pe
    from models import season_sim

ODDS_SIMS = 400          # per finished draft — plenty for a distribution read


def _my_pick(board_rows, drafted, team, log, overall, my_slot, teams, rounds):
    """Model policy: the live engine's anchor, exactly like mock_sim's model mode."""
    sl = pe.shortlist(board_rows, drafted, team.roster_dict() if hasattr(team, "roster_dict")
                      else {}, log, overall, teams=teams, my_slot=my_slot, rounds=rounds,
                      bench=[{"name": b["name"], "position": b["pos"]} for b in team.bench])
    return sl["anchor"]


class _Team(oa.TeamState):
    def roster_dict(self):
        return {s: ({"name": v["name"], "position": v["pos"]} if v else None)
                for s, v in self.slots.items()}


def run(board_rows, picks_log, team_order, overall_pick, my_slot=10, teams=10,
        rounds=18, n=12, profiles=None, alias_map=None) -> dict:
    me_name = team_order[my_slot - 1] if team_order and my_slot - 1 < len(team_order) else "ME"
    by_key = {}
    for r in board_rows:
        try:
            pts = float(r.get("league_pts") or 0)
        except (TypeError, ValueError):
            pts = 0.0
        by_key[(r.get("name"), pe.normalize_pos(r.get("pos")))] = pts

    def lookup(name, pos):
        return by_key.get((name, pos), 0.0)

    prof_of = {}
    for nm in (team_order or []):
        owner = (alias_map or {}).get(nm)
        prof_of[nm] = (profiles or {}).get(owner) if owner else None

    results = []
    my_player_counts = Counter()
    for s in range(n):
        rng = random.Random(4242 + s * 101)
        avail, states0, drafted = oa.build_state(board_rows, picks_log, team_order, teams)
        states = {nm: _Team(nm) for nm in team_order}
        # replay existing picks into upgraded state objects
        for nm, st in states0.items():
            if nm in states:
                states[nm].slots = st.slots
                states[nm].bench = st.bench
        log = list(picks_log or [])
        by_name = {}
        for r in avail:
            by_name.setdefault(r["name"], r)
        avail_set = {id(r) for r in avail}

        p = overall_pick
        last = rounds * teams
        while p <= last and avail:
            seat = oa.slot_for_pick(p, teams)
            nm = team_order[seat - 1]
            team = states[nm]
            rnd = (p - 1) // teams + 1
            if seat == my_slot:
                anchor = _my_pick(board_rows, drafted, team, log, p, my_slot, teams, rounds)
                chosen = by_name.get(anchor)
                if chosen is None or id(chosen) not in avail_set:
                    chosen, _ = oa.pick_one(team, avail, rnd, rounds, rng,
                                            prof_of.get(nm), log, teams)
            else:
                chosen, _ = oa.pick_one(team, avail, rnd, rounds, rng, prof_of.get(nm), log, teams)
            team.place(chosen)
            drafted.add(chosen["name"])
            avail.remove(chosen)
            avail_set.discard(id(chosen))
            log.append({"overall": p, "team": nm, "name": chosen["name"], "pos": chosen["pos"]})
            p += 1

        rosters = {nm: [] for nm in team_order}
        for pk in log:
            if pk["team"] in rosters:
                rosters[pk["team"]].append({"name": pk["name"], "position": pk["pos"]})
        odds = season_sim.title_odds(rosters, [], lookup, n_sims=ODDS_SIMS, seed=777 + s)
        me = odds.get(me_name, {})
        my_picks = [pk for pk in log if pk["team"] == me_name]
        seq = "-".join(pk["pos"] for pk in my_picks[:6])
        proj, _ = season_sim.lineup_points(rosters[me_name], lookup)
        for pk in my_picks[:10]:
            my_player_counts[f'{pk["name"]} ({pk["pos"]})'] += 1
        results.append({"seed": s, "title": me.get("title", 0), "playoff": me.get("playoff", 0),
                        "expWins": me.get("expWins", 0), "proj": round(proj),
                        "seq": seq, "myPicks": my_picks})

    results.sort(key=lambda r: -r["title"])
    titles = sorted(r["title"] for r in results)
    med = titles[len(titles) // 2]
    seq_stats = {}
    for r in results:
        s4 = "-".join(r["seq"].split("-")[:4])
        seq_stats.setdefault(s4, []).append(r["title"])
    sequences = sorted(
        ({"seq": k, "n": len(v), "meanTitle": round(sum(v) / len(v), 4)}
         for k, v in seq_stats.items()),
        key=lambda x: -x["meanTitle"])
    best = results[0]
    return {
        "n": n,
        "titleMin": titles[0], "titleMedian": med, "titleMax": titles[-1],
        "titles": [r["title"] for r in results],
        "projRange": [min(r["proj"] for r in results), max(r["proj"] for r in results)],
        "sequences": sequences[:6],
        "mostDrafted": [{"player": k, "n": c} for k, c in my_player_counts.most_common(8)],
        "bestDraft": {"title": best["title"], "proj": best["proj"], "seq": best["seq"],
                      "picks": [{"overall": pk["overall"], "name": pk["name"], "pos": pk["pos"]}
                                for pk in best["myPicks"]]},
    }


if __name__ == "__main__":     # python models/tournament.py — self-test
    import csv
    import json as _json
    import time as _time
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    rows = list(csv.DictReader(open(ROOT / "data/processed/board_2026.csv", encoding="utf-8")))
    profiles = _json.loads((ROOT / "data/processed/manager_profiles.json").read_text(encoding="utf-8"))
    aliases = {k: v for k, v in _json.loads((ROOT / "data/team_aliases.json").read_text(
        encoding="utf-8")).items() if not k.startswith("_")}
    order = ["TGil", "Blake", "Nico", "D-Put", "Lamb", "Flowers", "Spivey", "Will", "Munford", "JRay"]
    t0 = _time.time()
    res = run(rows, [], order, 1, my_slot=10, n=8, profiles=profiles, alias_map=aliases)
    print(f"WAR GAMES ×{res['n']} in {_time.time()-t0:.1f}s")
    print(f"  title odds: min {res['titleMin']:.1%} · median {res['titleMedian']:.1%} · max {res['titleMax']:.1%}")
    print(f"  proj range: {res['projRange']}")
    print("  sequences (first 4 rounds):")
    for s in res["sequences"]:
        print(f"    {s['seq']:<16} ×{s['n']}  mean title {s['meanTitle']:.1%}")
    print("  model keeps buying:", ", ".join(f"{m['player']}×{m['n']}" for m in res["mostDrafted"][:5]))
    print(f"  best draft ({res['bestDraft']['title']:.1%}): {res['bestDraft']['seq']} …")
    assert res["titleMedian"] > 0.05, "median title odds implausibly low"
    print("SELF-TEST PASSED")
