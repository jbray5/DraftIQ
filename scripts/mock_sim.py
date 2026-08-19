"""Mock-draft simulator: DraftIQ model vs FantasyPros ECR, in the same drafts.

Simulates full 18-round, 10-team snake drafts of the 2026 board:
  * Opponents pick ADP-realistically with noise + their REAL tendencies
    (Street reaches QB ~rd3, Munford RB-heavy, Hubauer/Gilbert wait on QB) and sane
    roster rules (fill starters, stream K/DST/IDP late, never 2 streamers).
  * Ray is driven by a POLICY:
      - "model": models/pick_engine.shortlist anchor (the live engine, no LLM)
      - "fp":    need-aware best-available by FantasyPros ECR (their board;
                 IDP is invisible to ECR so it back-fills IDP from ours late)
  * In every model run, at each Ray pick BOTH advisors are consulted on the
    IDENTICAL board state — divergences are logged with the engine's reasoning.

Output: data/processed/mock_sim_report.md + console summary.
Run: python scripts/mock_sim.py
"""
from __future__ import annotations

import csv
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "models"))
import pick_engine as pe  # noqa: E402

TEAMS = ["Gilbert", "Bollinger", "Hubauer", "Putman", "Walker", "Wester", "Spivey", "Street", "Munford", "Ray"]
MY_SLOT = 10   # the wheel — real 2026 draft order
NT, ROUNDS = 10, 18
SLOTS = ["QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "FLEX2", "IDP", "DST", "K"]
FLEX_ELIG = ("RB", "WR", "TE")
STREAM = {"IDP", "DST", "K"}
# manager priors (mirrors data/processed/manager_profiles.json)
QB_EARLY = {"Street"}
QB_LATE = {"Hubauer", "Gilbert"}
RB_HEAVY = {"Munford"}

DSTS = [("Ravens D/ST", "BAL"), ("Broncos D/ST", "DEN"), ("Texans D/ST", "HOU"),
        ("Eagles D/ST", "PHI"), ("Steelers D/ST", "PIT"), ("Bills D/ST", "BUF"),
        ("Vikings D/ST", "MIN"), ("Chargers D/ST", "LAC"), ("Lions D/ST", "DET"),
        ("49ers D/ST", "SF"), ("Packers D/ST", "GB"), ("Chiefs D/ST", "KC"),
        ("Seahawks D/ST", "SEA"), ("Saints D/ST", "NO"), ("Browns D/ST", "CLE"),
        ("Bears D/ST", "CHI")]


def load_board() -> list[dict]:
    rows = list(csv.DictReader(open(ROOT / "data/processed/board_2026.csv", encoding="utf-8")))
    if not any(r.get("pos") == "DST" for r in rows):   # legacy fallback — board carries real D/ST now (models/dst.py)
        for i, (nm, tm) in enumerate(DSTS):
            rows.append({"name": nm, "team": tm, "pos": "DST", "league_pts": str(120 - i * 2),
                         "vorp": "-2", "draft_value": "-0.4", "adp": str(165 + i * 2),
                         "ecr": str(164 + i * 3), "sos": "", "ecr_tier": ""})
    return rows


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def slot_for_pick(p: int) -> int:
    r = (p - 1) // NT + 1
    q = (p - 1) % NT + 1
    return q if r % 2 == 1 else NT - q + 1


class Team:
    def __init__(self, name):
        self.name = name
        self.slots = {s: None for s in SLOTS}
        self.bench: list[dict] = []

    def place(self, player):
        pos = player["pos"]
        opts = {"RB": ["RB1", "RB2", "FLEX", "FLEX2"], "WR": ["WR1", "WR2", "FLEX", "FLEX2"],
                "TE": ["TE", "FLEX", "FLEX2"]}.get(pos, [pos])
        for s in opts:
            if s in self.slots and self.slots[s] is None:
                self.slots[s] = player
                return s
        self.bench.append(player)
        return "BN"

    def count(self, pos):
        n = sum(1 for v in self.slots.values() if v and v["pos"] == pos)
        return n + sum(1 for b in self.bench if b["pos"] == pos)

    def open_starters(self):
        out = []
        for s, v in self.slots.items():
            if v is None:
                out.append(pe.SLOT_BASE[s] or "FLEX")
        return out

    def roster_dict(self):
        return {s: ({"name": v["name"], "position": v["pos"]} if v else None)
                for s, v in self.slots.items()}


def opponent_pick(team: Team, avail: list[dict], rnd: int, rng: random.Random) -> dict:
    """ADP-realistic with noise, need-awareness, streamer timing, and manager priors."""
    cands = sorted(avail, key=lambda p: f(p.get("adp"), 999))[:10]
    open_s = team.open_starters()
    picks_left = ROUNDS - rnd + 1
    empty_stream = sum(1 for s in ("IDP", "DST", "K") if team.slots[s] is None)
    weights = []
    for i, c in enumerate(cands):
        w = [4.0, 3.0, 2.2, 1.6, 1.2, 0.9, 0.7, 0.5, 0.4, 0.3][i]
        pos = c["pos"]
        if pos in STREAM:
            if team.slots[pos] is not None:
                w *= 0.001                              # never a 2nd streamer
            elif pos == "IDP" and rnd >= 8:
                w *= 1.2                                 # league drafts IDP mid-late
            elif picks_left > empty_stream + 2:
                w *= 0.02                                # too early to stream
        elif pos == "QB":
            have = team.count("QB")
            if have >= 1:
                w *= 0.02 if rnd < 12 else 0.3           # rarely a 2nd QB early
            elif team.name in QB_EARLY and rnd >= 2:
                w *= 5.0
            elif team.name in QB_LATE and rnd < 6:
                w *= 0.15
        elif pos == "TE" and team.count("TE") >= 1:
            w *= 0.15
        elif pos in ("RB", "WR"):
            if team.name in RB_HEAVY and pos == "RB" and rnd <= 4:
                w *= 2.2
            if pos not in open_s and "FLEX" not in open_s and team.count(pos) >= 6:
                w *= 0.2
        # force-fill: if remaining picks barely cover empty starters, prioritize them
        if picks_left <= len([s for s in team.slots.values() if s is None]) and \
           pos in open_s:
            w *= 3.0
        weights.append(max(w, 1e-6))
    return rng.choices(cands, weights=weights, k=1)[0]


def _fills_empty(team: Team, pos: str) -> bool:
    if pos in ("RB", "WR", "TE"):
        opts = {"RB": ["RB1", "RB2", "FLEX", "FLEX2"], "WR": ["WR1", "WR2", "FLEX", "FLEX2"],
                "TE": ["TE", "FLEX", "FLEX2"]}[pos]
        return any(team.slots[s] is None for s in opts)
    return pos in team.slots and team.slots[pos] is None


def fp_pick(team: Team, avail: list[dict], rnd: int) -> dict:
    """Need-aware best-available by FantasyPros ECR (their assistant's behavior):
    BPA by expert rank, no doubled streamers, and it WILL fill every starting slot
    by the end (a human following FP does not leave K empty)."""
    picks_left = ROUNDS - rnd + 1
    n_empty = sum(1 for v in team.slots.values() if v is None)
    must_fill = picks_left <= n_empty            # endgame: every pick must fill a slot
    ranked = sorted([p for p in avail if f(p.get("ecr"))], key=lambda p: f(p["ecr"]))
    for c in ranked:
        pos = c["pos"]
        if must_fill and not _fills_empty(team, pos):
            continue
        if pos in STREAM:
            if team.slots[pos] is not None:
                continue
            if not must_fill and rnd < 13:       # FP ranks K/DST 164+; nobody takes them early
                continue
        elif pos == "QB" and team.count("QB") >= (1 if rnd < 12 else 2):
            continue
        elif pos == "TE" and team.count("TE") >= (1 if rnd < 12 else 2):
            continue
        return c
    # ECR exhausted or only IDP slots left (invisible to FP): fall back to board value
    left = sorted(avail, key=lambda p: -(f(p.get("draft_value"), -99)))
    for c in left:
        if must_fill and not _fills_empty(team, c["pos"]):
            continue
        if c["pos"] in STREAM and team.slots[c["pos"]] is not None:
            continue
        return c
    return avail[0]


def model_pick(team: Team, board, drafted, picks_log, overall, my_slot):
    sl = pe.shortlist(board, drafted, team.roster_dict(), picks_log, overall,
                      teams=NT, team_order=TEAMS,
                      team_rosters=None, my_slot=my_slot, rounds=ROUNDS,
                      bench=[{"name": b["name"], "position": b["pos"]} for b in team.bench])
    return sl


def run_draft(policy: str, seed: int, my_slot: int = MY_SLOT):
    rng = random.Random(seed)
    board = load_board()
    by_name = {}
    for r in board:
        r["pos"] = pe.normalize_pos(r.get("pos") or r.get("position"))
        r["name_"] = r["name"]
        by_name[r["name"]] = r
    avail = list(board)
    teams = {i + 1: Team(TEAMS[i]) for i in range(NT)}
    drafted: set[str] = set()
    picks_log: list[dict] = []
    my_log = []

    for overall in range(1, ROUNDS * NT + 1):
        seat = slot_for_pick(overall)
        team = teams[seat]
        rnd = (overall - 1) // NT + 1
        if seat == my_slot:
            fp_c = fp_pick(team, avail, rnd)
            if policy == "model":
                sl = model_pick(team, board, drafted, picks_log, overall, my_slot)
                anchor = sl["anchor"]
                chosen = by_name.get(anchor) or fp_c
                info = next((c for c in sl["shortlist"] if c["name"] == anchor), {})
                my_log.append({
                    "overall": overall, "round": rnd,
                    "model": chosen["name"], "model_pos": chosen["pos"],
                    "fits": info.get("fits"), "urgency": info.get("urgency"),
                    "why": info.get("why"), "flat": sl.get("flat"),
                    "model_ecr": f(chosen.get("ecr")), "model_vorp": f(chosen.get("vorp")),
                    "fp": fp_c["name"], "fp_pos": fp_c["pos"], "fp_ecr": f(fp_c.get("ecr")),
                })
            else:
                chosen = fp_c
                my_log.append({"overall": overall, "round": rnd,
                               "model": None, "fp": chosen["name"], "fp_pos": chosen["pos"]})
        else:
            chosen = opponent_pick(team, avail, rnd, rng)
        team.place(chosen)
        drafted.add(chosen["name"])
        avail.remove(chosen)
        picks_log.append({"overall": overall, "team": team.name,
                          "name": chosen["name"], "pos": chosen["pos"]})
    return teams[my_slot], my_log


def evaluate(team: Team):
    starters = [v for v in team.slots.values() if v]
    proj = sum(f(v.get("league_pts"), 0) or 0 for v in starters)
    vorp = sum(f(v.get("vorp"), 0) or 0 for v in starters)
    empty = [s for s, v in team.slots.items() if v is None]
    return {"proj": round(proj), "vorp": round(vorp), "empty": empty,
            "counts": {p: team.count(p) for p in ("QB", "RB", "WR", "TE", "IDP", "DST", "K")}}


def main():
    seeds = [7, 21, 42]
    lines = ["# Mock-draft simulations — DraftIQ model vs FantasyPros ECR",
             "", f"{len(seeds)} paired simulations · 10-team snake · 18 rounds · Ray at slot 10 "
             "(THE WHEEL — real 2026 order) · opponents ADP-realistic with real manager tendencies", ""]
    agg_div, agg_res = [], []
    for seed in seeds:
        model_team, mlog = run_draft("model", seed)
        fp_team, _ = run_draft("fp", seed)
        em, ef = evaluate(model_team), evaluate(fp_team)
        agg_res.append((seed, em, ef))
        divs = [m for m in mlog if m["model"] != m["fp"]]
        agg_div += [{**d, "seed": seed} for d in divs]
        lines.append(f"## Sim {seed}: model {em['proj']} proj / {em['vorp']} VORP · "
                     f"FP-follower {ef['proj']} proj / {ef['vorp']} VORP")
        lines.append(f"- model roster: {em['counts']} empty={em['empty'] or 'none'}")
        lines.append(f"- FP roster:    {ef['counts']} empty={ef['empty'] or 'none'}")
        lines.append(f"- agreed on {len(mlog)-len(divs)}/{len(mlog)} picks; divergences:")
        lines.append("")
        lines.append("| Rd | DraftIQ took | FP-ECR said | why the model differed |")
        lines.append("|---|---|---|---|")
        for d in divs:
            why = []
            if d["fits"] and d["fits"] not in ("BENCH",):
                why.append(f"fills {d['fits']}")
            if d["urgency"] == "pick-now":
                why.append("won't survive")
            if d["flat"]:
                why.append("flat zone")
            if d["fp_pos"] in STREAM and d["model_pos"] not in STREAM:
                why.append("streamer demoted")
            if d["model_pos"] == "IDP":
                why.append("IDP invisible to FP")
            if d["model_ecr"] and d["fp_ecr"] and d["model_ecr"] > d["fp_ecr"] + 15:
                why.append("league-scoring value > national rank")
            lines.append(f"| {d['round']} | {d['model']} ({d['model_pos']}) | "
                         f"{d['fp']} ({d['fp_pos']}) | {'; '.join(why) or d['why'] or '—'} |")
        lines.append("")
    # aggregate
    n_picks = len(seeds) * ROUNDS
    lines.insert(3, f"**Agreement: {n_picks - len(agg_div)}/{n_picks} of Ray's picks; "
                 f"{len(agg_div)} divergences.** Model roster beat the FP-follower roster in "
                 f"{sum(1 for _, a, b in agg_res if a['vorp'] > b['vorp'])}/{len(seeds)} sims on starter VORP.")
    out = ROOT / "data/processed/mock_sim_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:14]))
    print(f"...full report -> {out.relative_to(ROOT)}")
    # console: divergence archetypes
    from collections import Counter
    arch = Counter()
    for d in agg_div:
        if d["model_pos"] == "IDP":
            arch["model drafts IDP (FP can't see the position)"] += 1
        elif d["fp_pos"] in STREAM and d["model_pos"] not in STREAM:
            arch["FP forced a streamer early; model took skill"] += 1
        elif d["fits"] and d["fits"] != "BENCH":
            arch[f"model fills a starter ({d['fits']}) over FP's name-value"] += 1
        else:
            arch["value/ordering disagreement"] += 1
    print("\nDivergence archetypes:")
    for k, v in arch.most_common():
        print(f"  {v:>2}x {k}")


if __name__ == "__main__":
    main()
