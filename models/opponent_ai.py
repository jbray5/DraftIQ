"""Simulated opponents for DraftIQ — the room, drafting itself.

One engine, three consumers:
  * GHOST DRAFT  (/api/sim/opponents) — auto-play every opponent pick until the
    user is back on the clock, so draft night can be rehearsed against this room.
  * SPY dossiers (/api/spy)           — "predicted next picks" for one opponent.
  * WHEEL planner (models/wheel.py)   — Monte-Carlo the 18-pick wait between the
    user's back-to-back turns to get real survival odds per player.

Behavior model (evolved from scripts/mock_sim.py's opponent_pick, plus fixes it
lacked — need-injection for ADP-less IDPs, endgame must-fill, bench-shopping):
ADP-realistic candidate window with rank-decayed weights, roster-need awareness,
streamer timing (never a 2nd K/DST/IDP, never early), endgame force-fill, and
per-manager priors read from data/processed/manager_profiles.json — Will reaches
for QBs, Munford hammers RBs, Nico/TGil wait on QB — weighted by profile
confidence exactly like the live pick engine does.

Deterministic per seed. No LLM, no network.
"""
from __future__ import annotations

import random

try:
    from pick_engine import normalize_pos, profile_prior, run_heat
except ImportError:  # pragma: no cover
    from models.pick_engine import normalize_pos, profile_prior, run_heat

SLOTS = ["QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "FLEX2", "IDP", "DST", "K"]
SLOT_OPTS = {"RB": ["RB1", "RB2", "FLEX", "FLEX2"], "WR": ["WR1", "WR2", "FLEX", "FLEX2"],
             "TE": ["TE", "FLEX", "FLEX2"]}
STREAM = {"IDP", "DST", "K"}
RANK_W = [4.0, 3.0, 2.2, 1.6, 1.2, 0.9, 0.7, 0.5, 0.4, 0.3, 0.25, 0.2]


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


class TeamState:
    """Roster slots + bench for one simulated team, rebuilt from the live pick log."""

    def __init__(self, name: str):
        self.name = name
        self.slots: dict[str, dict | None] = {s: None for s in SLOTS}
        self.bench: list[dict] = []

    def place(self, player: dict) -> str:
        pos = player["pos"]
        for s in SLOT_OPTS.get(pos, [pos]):
            if s in self.slots and self.slots[s] is None:
                self.slots[s] = player
                return s
        self.bench.append(player)
        return "BN"

    def count(self, pos: str) -> int:
        n = sum(1 for v in self.slots.values() if v and v["pos"] == pos)
        return n + sum(1 for b in self.bench if b["pos"] == pos)

    def open_starters(self) -> list[str]:
        base = {"RB1": "RB", "RB2": "RB", "WR1": "WR", "WR2": "WR",
                "FLEX": "FLEX", "FLEX2": "FLEX"}
        return [base.get(s, s) for s, v in self.slots.items() if v is None]

    def n_empty(self) -> int:
        return sum(1 for v in self.slots.values() if v is None)


def slot_for_pick(p: int, teams: int) -> int:
    r = (p - 1) // teams + 1
    q = (p - 1) % teams + 1
    return q if r % 2 == 1 else teams - q + 1


def build_state(board_rows: list[dict], picks_log: list[dict], team_order: list[str],
                teams: int) -> tuple[list[dict], dict[str, TeamState], set[str]]:
    """(available rows, per-team states, drafted-name set) from the live pick log.

    Names are NOT unique on this board (e.g. Josh Allen QB/BUF and Josh Allen
    LB — 1110 IDP rows make collisions routine), so lookups prefer the row whose
    position matches the pick log's position before falling back to name-only."""
    by_name: dict[str, list[dict]] = {}
    pool = []
    for r in board_rows:
        row = {**r, "pos": normalize_pos(r.get("pos") or r.get("position"))}
        by_name.setdefault(str(r.get("name")), []).append(row)
        pool.append(row)
    states = {nm: TeamState(nm) for nm in team_order}
    drafted: set[str] = set()
    for pk in picks_log or []:
        nm = pk.get("name")
        drafted.add(nm)
        t = pk.get("team")
        cand = by_name.get(nm) or []
        want = normalize_pos(pk.get("pos")) if pk.get("pos") else None
        row = (next((r for r in cand if want and r["pos"] == want), None)
               or (cand[0] if cand else {"name": nm, "pos": want or "IDP", "adp": None}))
        if t in states:
            states[t].place(row)
    avail = [r for r in pool if r["name"] not in drafted]
    return avail, states, drafted


def _prior_mult(prof: dict | None, pos: str, rnd: int, team: TeamState) -> tuple[float, str]:
    """Manager-tendency multiplier + a short label when it fires."""
    if not prof or prof.get("confidence") == "low":
        return 1.0, ""
    if pos == "QB" and team.count("QB") == 0:
        q = prof.get("qb_first_round_avg")
        if q is not None and q <= 4.1 and rnd >= 2:
            return 4.0, f"QB habit (hist ~rd {q:.1f})"
        if q is not None and q >= 6.1 and rnd < 6:
            return 0.15, ""
    if pos == "RB" and rnd <= 4 and (prof.get("rb_in_first4") or 0) >= 2.2:
        return 2.2, "RB-heavy history"
    if pos == "WR" and rnd <= 4 and (prof.get("wr_in_first4") or 0) >= 2.2:
        return 2.0, "WR-leaning history"
    return 1.0, ""


def _best_at(avail: list[dict], pos: str, n: int = 2) -> list[dict]:
    """Best available at a position by projected league points (ADP-blind — most IDP
    and some late K carry no ADP at all, so an ADP window never surfaces them)."""
    at = [r for r in avail if r["pos"] == pos]
    at.sort(key=lambda r: -(_f(r.get("league_pts")) or _f(r.get("points")) or 0))
    return at[:n]


def candidates(team: TeamState, avail: list[dict], rnd: int, rounds: int,
               prof: dict | None = None, picks_log: list[dict] | None = None,
               teams: int = 10, window: int = 12) -> list[tuple[dict, float, str]]:
    """The pick distribution for one opponent: [(player_row, weight, why)]."""
    cands = sorted(avail, key=lambda p: _f(p.get("adp"), 999))[:window]
    open_s = team.open_starters()
    picks_left = rounds - rnd + 1
    empty_stream = sum(1 for s in STREAM if team.slots[s] is None)
    heat = run_heat(picks_log or [], teams)

    # The ADP window can't see players without an ADP (nearly all IDP). Inject the
    # best available at every empty streamer slot once its window opens, so rosters
    # actually complete: IDP from rd 8 (this room's habit), DST/K when time is short.
    inject: list[dict] = []
    if team.slots["IDP"] is None and rnd >= 8:
        inject += _best_at(avail, "IDP")
    for pos in ("DST", "K"):
        if team.slots[pos] is None and picks_left <= empty_stream + 3:
            inject += _best_at(avail, pos)
    seen_ids = {id(c) for c in cands}
    cands += [c for c in inject if id(c) not in seen_ids]

    # Bench-shopping (all starters filled): the ADP window late in a draft is mostly
    # ADP-less IDPs, and every one of them is a near-zero-weight "2nd streamer" — a
    # weighted choice over all-tiny weights still fires. Shop skill depth instead.
    if team.n_empty() == 0:
        skill = [c for c in cands if c["pos"] in ("RB", "WR", "TE", "QB")]
        for pos in ("RB", "WR", "TE"):
            have = {id(c) for c in skill}
            skill += [c for c in _best_at(avail, pos) if id(c) not in have]
        if skill:
            cands = skill[:window]

    # Endgame law: when remaining picks just cover the empty starters, every pick
    # MUST fill one — restrict the field to fillers (bench picks are no longer legal).
    if picks_left <= team.n_empty():
        fillers = [c for c in cands
                   if c["pos"] in open_s or (c["pos"] in ("RB", "WR", "TE") and "FLEX" in open_s)]
        for pos in set(open_s):
            if pos in ("FLEX",):
                continue
            if not any(c["pos"] == pos for c in fillers):
                fillers += _best_at(avail, pos, 1)      # nothing in window fills it → fetch one
        if fillers:
            cands = fillers
    out = []
    for i, c in enumerate(cands):
        w = RANK_W[i] if i < len(RANK_W) else 0.15
        pos = c["pos"]
        why = f"ADP {(_f(c.get('adp')) or 0):.0f}" if _f(c.get("adp")) else "best at need"
        if pos in STREAM:
            if team.slots[pos] is not None:
                w *= 0.001                                 # never a 2nd streamer
            elif picks_left <= empty_stream + 1:
                w = max(w, 2.5)                            # out of slack — take the streamer
                why = f"forced fill: open {pos}"
            elif pos == "IDP" and rnd >= 8:
                w = max(w * 1.2, 0.9)                      # this room drafts IDP mid-late
                why = "IDP window (rd 8+)"
            elif picks_left > empty_stream + 2:
                w *= 0.02                                  # too early to stream
        elif pos == "QB":
            if team.count("QB") >= 1:
                w *= 0.02 if rnd < 12 else 0.3
            else:
                m, tag = _prior_mult(prof, pos, rnd, team)
                w *= m
                if tag:
                    why = tag
        elif pos == "TE" and team.count("TE") >= 1:
            w *= 0.15
        elif pos in ("RB", "WR"):
            m, tag = _prior_mult(prof, pos, rnd, team)
            w *= m
            if tag:
                why = tag
            if pos not in open_s and "FLEX" not in open_s and team.count(pos) >= 6:
                w *= 0.2
            # mild run-following: humans chase a hot position
            h = heat.get(pos, 1.0)
            if h >= 1.8:
                w *= 1.25
                why = f"chasing the {pos} run"
        if pos in open_s or (pos in ("RB", "WR", "TE") and "FLEX" in open_s):
            if picks_left <= team.n_empty():
                w *= 3.0                                    # endgame: must fill starters
                why = f"forced fill: open {pos}"
            elif pos in open_s and pos in ("RB", "WR", "TE", "QB"):
                why = f"fills open {pos}"
        out.append((c, max(w, 1e-6), why))
    return out


def pick_one(team: TeamState, avail: list[dict], rnd: int, rounds: int,
             rng: random.Random, prof: dict | None = None,
             picks_log: list[dict] | None = None, teams: int = 10) -> tuple[dict, str]:
    cw = candidates(team, avail, rnd, rounds, prof, picks_log, teams)
    rows = [c for c, _, _ in cw]
    weights = [w for _, w, _ in cw]
    chosen = rng.choices(rows, weights=weights, k=1)[0]
    why = next(y for c, _, y in cw if c is chosen)
    return chosen, why


def predict(team: TeamState, avail: list[dict], rnd: int, rounds: int,
            prof: dict | None = None, picks_log: list[dict] | None = None,
            teams: int = 10, top: int = 3) -> list[dict]:
    """Spy mode: the opponent's most likely next picks, with probabilities."""
    cw = candidates(team, avail, rnd, rounds, prof, picks_log, teams)
    total = sum(w for _, w, _ in cw) or 1.0
    ranked = sorted(cw, key=lambda x: -x[1])[:top]
    return [{"name": c["name"], "pos": c["pos"], "team": c.get("team"),
             "adp": _f(c.get("adp")), "prob": round(w / total, 3), "why": y}
            for c, w, y in ranked]


def simulate(board_rows: list[dict], picks_log: list[dict], team_order: list[str],
             overall_pick: int, my_slot: int, teams: int = 10, rounds: int = 18,
             profiles: dict | None = None, alias_map: dict | None = None,
             seed: int | None = None, stop_at_my_pick: bool = True) -> dict:
    """Play the room forward from `overall_pick`. Stops when the user's seat is up
    (or the draft ends). Returns the picks it made, each with a one-line why."""
    rng = random.Random(seed if seed is not None else overall_pick * 7919)
    avail, states, _drafted = build_state(board_rows, picks_log, team_order, teams)
    prof_of = {}
    for nm in team_order:
        owner = (alias_map or {}).get(nm)
        prof_of[nm] = (profiles or {}).get(owner) if owner else None

    log = list(picks_log or [])
    made = []
    p = overall_pick
    last = rounds * teams
    while p <= last:
        seat = slot_for_pick(p, teams)
        if stop_at_my_pick and seat == my_slot:
            break
        name = team_order[seat - 1] if seat - 1 < len(team_order) else f"Seat{seat}"
        team = states.setdefault(name, TeamState(name))
        rnd = (p - 1) // teams + 1
        if not avail:
            break
        chosen, why = pick_one(team, avail, rnd, rounds, rng, prof_of.get(name), log, teams)
        team.place(chosen)
        avail.remove(chosen)
        entry = {"overall": p, "team": name, "seat": seat, "round": rnd,
                 "name": chosen["name"], "pos": chosen["pos"], "why": why,
                 "playerId": chosen.get("playerId")}
        made.append(entry)
        log.append(entry)
        p += 1
    return {"picks": made, "nextPick": p, "done": p > last}


if __name__ == "__main__":     # python models/opponent_ai.py — quick self-test
    import csv
    import json
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    rows = list(csv.DictReader(open(ROOT / "data/processed/board_2026.csv", encoding="utf-8")))
    profiles = json.loads((ROOT / "data/processed/manager_profiles.json").read_text(encoding="utf-8"))
    aliases = {k: v for k, v in json.loads(
        (ROOT / "data/team_aliases.json").read_text(encoding="utf-8")).items()
        if not k.startswith("_")}
    order = ["TGil", "Blake", "Nico", "D-Put", "Lamb", "Flowers", "Spivey", "Will", "Munford", "JRay"]

    res = simulate(rows, [], order, 1, my_slot=10, seed=42,
                   profiles=profiles, alias_map=aliases)
    print(f"simulated {len(res['picks'])} picks to my first turn (pick {res['nextPick']}):")
    for pk in res["picks"]:
        print(f"  #{pk['overall']:<3} {pk['team']:<8} {pk['name']:<24} {pk['pos']:<4} · {pk['why']}")

    # full-draft smoke: nobody may end with an empty starter or a doubled streamer
    res2 = simulate(rows, [], order, 1, my_slot=99, seed=7, stop_at_my_pick=False)
    avail, states, _ = build_state(rows, res2["picks"], order, 10)
    bad = 0
    for nm, st in states.items():
        empty = [s for s, v in st.slots.items() if v is None]
        dbl = [p for p in STREAM if st.count(p) > 1]
        if empty or dbl:
            bad += 1
            print(f"  PROBLEM {nm}: empty={empty} doubled={dbl}")
    print(f"\nfull 18-round sim: {len(res2['picks'])} picks; "
          f"{'ALL 10 ROSTERS LEGAL' if not bad else f'{bad} bad rosters'}")
