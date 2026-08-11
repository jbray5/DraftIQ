"""DraftIQ value engine.

Replaces the placeholder "70% ADP" composite with a real draft model:

  VORP   value over replacement, with league-correct replacement levels
  VONA   value over next available — the drop-off to YOUR next pick, not the
         next player overall (this is what actually drives snake-draft decisions)
  survival   P(player still on the board at your next pick), from ADP vs the clock
  scarcity   tier cliff at the position right now

These combine into a single, tunable DraftIQ score. The *structure* here is
final; the *weights* and replacement baselines get calibrated to this league's
real scoring and draft history once the ESPN pull lands (see models/README.md and
the `backtest` skill). Everything is pure and deterministic so it's testable.

Player input is the shape from /api/rankings:
  {name, team, position, points, adp, rank}
where `points` is projected fantasy points in league scoring (today: SportsData;
after calibration: recomputed from component stats under the league's scoring).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# League configuration (this league's reality; override per season from the pull)
# --------------------------------------------------------------------------- #
@dataclass
class League:
    teams: int = 12
    # starting slots that aren't flex (each consumes the best N at that position)
    starters: dict = field(default_factory=lambda: {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DST": 1, "IDP": 1,
    })
    flex_slots: int = 1
    flex_eligible: tuple = ("RB", "WR", "TE")
    # how a FLEX slot tends to be filled across eligible positions
    flex_share: dict = field(default_factory=lambda: {"RB": 0.45, "WR": 0.45, "TE": 0.10})


OFFENSE_SPECIAL = {"QB", "RB", "WR", "TE", "K", "DST"}
# Streamability: IDP/K/D-ST are largely waiver-streamable in this league, so their
# raw VORP overstates draft priority. Discount their draft value so they sink down
# the board while an elite outlier can still surface as a mid-late value. (Tunable;
# user chose "streamable discount" — see CLAUDE.md.)
STREAM_DISCOUNT = {"IDP": 0.35, "K": 0.10, "DST": 0.20}

POS_ALIASES = {
    "qb": "QB", "rb": "RB", "fb": "RB", "wr": "WR", "te": "TE",
    "k": "K", "pk": "K",
    "dst": "DST", "def": "DST", "d": "DST", "defense": "DST",
}


def normalize_pos(pos: str) -> str:
    raw = (pos or "").lower().replace(".", "").replace(" ", "").replace("/", "")
    if not raw:
        return ""
    if raw in POS_ALIASES:
        return POS_ALIASES[raw]
    if raw.upper() in OFFENSE_SPECIAL:
        return raw.upper()
    # any other real NFL position is a defensive player -> this league's DP/IDP slot
    return "IDP"


def _by_pos(players: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in players:
        out.setdefault(normalize_pos(p.get("position")), []).append(p)
    for lst in out.values():
        lst.sort(key=lambda x: (x.get("points") is None, -(x.get("points") or 0)))
    return out


def _pts(p: dict) -> float:
    v = p.get("points")
    return float(v) if isinstance(v, (int, float)) else 0.0


# --------------------------------------------------------------------------- #
# Replacement levels — the crux for an IDP / 2-RB-2-WR-FLEX league
# --------------------------------------------------------------------------- #
def replacement_baselines(league: League) -> dict[str, float]:
    """How many players at each position are 'startable' across the whole league.
    A position's replacement level is the points of the player just past that count.
    FLEX is distributed across eligible positions by flex_share."""
    counts: dict[str, float] = {pos: league.teams * n for pos, n in league.starters.items()}
    for pos in league.flex_eligible:
        counts[pos] = counts.get(pos, 0) + league.teams * league.flex_slots * league.flex_share.get(pos, 0)
    return counts


def replacement_levels(players: list[dict], league: League) -> dict[str, float]:
    by_pos = _by_pos(players)
    counts = replacement_baselines(league)
    levels: dict[str, float] = {}
    for pos, lst in by_pos.items():
        # index of the first non-startable player = replacement level
        idx = max(0, min(len(lst) - 1, int(round(counts.get(pos, league.teams)))))
        levels[pos] = _pts(lst[idx]) if lst else 0.0
    return levels


# --------------------------------------------------------------------------- #
# Pick-survival — P(still available at my next pick)
# --------------------------------------------------------------------------- #
def picks_until_next(overall_pick: int, teams: int) -> int:
    """In a snake draft, gap from this pick to the same seat's next pick.
    Must respect that even rounds run in reverse seat order."""
    r = (overall_pick - 1) // teams + 1        # 1-indexed round
    q = (overall_pick - 1) % teams + 1         # 1-indexed position within the round
    seat = q if r % 2 == 1 else teams - q + 1  # the drafting seat (1..teams)
    if r % 2 == 1:                             # odd round -> next round runs reversed
        nxt = r * teams + (teams - seat + 1)
    else:                                      # even round -> next round runs forward
        nxt = r * teams + seat
    return nxt - overall_pick


def survival(adp: float | None, next_pick_number: int, steepness: float = 0.18) -> float:
    """Logistic on (adp - next_pick): players whose ADP is well past your next pick
    are very likely to survive; those before it usually won't."""
    if adp is None:
        return 0.5
    return 1.0 / (1.0 + math.exp(-steepness * (adp - next_pick_number)))


# --------------------------------------------------------------------------- #
# Board ranking
# --------------------------------------------------------------------------- #
def rank_board(players: list[dict], league: League | None = None,
               overall_pick: int = 1, weights: dict | None = None) -> list[dict]:
    """Annotate every available player with vorp / vona / survival / scarcity and a
    single DraftIQ score, sorted best-first. `players` should be the players still
    available (drafted ones removed)."""
    league = league or League()
    w = {"vorp": 0.55, "vona": 0.30, "scarcity": 0.15, **(weights or {})}

    by_pos = _by_pos(players)
    levels = replacement_levels(players, league)
    gap = picks_until_next(overall_pick, league.teams)
    next_pick_number = overall_pick + gap

    out = []
    for p in players:
        pos = normalize_pos(p.get("position"))
        lst = by_pos.get(pos, [])
        rank_in_pos = lst.index(p) if p in lst else len(lst)
        repl = levels.get(pos, 0.0)
        vorp = _pts(p) - repl

        # VONA: value over the best you'd expect to still be available at THIS
        # position by your next pick (≈ slide `gap`-worth of positional picks down).
        # Approximate the next-available as the player ~gap/len(positions) deeper.
        slide = max(1, gap // max(1, len(by_pos)))
        nxt_idx = min(len(lst) - 1, rank_in_pos + slide)
        vona = _pts(p) - (_pts(lst[nxt_idx]) if lst else 0.0)

        # Scarcity / tier cliff: drop to the very next player at the position.
        cliff = _pts(p) - (_pts(lst[rank_in_pos + 1]) if rank_in_pos + 1 < len(lst) else _pts(p))

        surv = survival(p.get("adp"), next_pick_number)

        out.append({**p, "pos": pos, "vorp": round(vorp, 2), "vona": round(vona, 2),
                    "cliff": round(cliff, 2), "survival": round(surv, 3)})

    # normalize the three value signals to 0..1 across the available pool, then blend
    def _norm(key):
        vals = [r[key] for r in out]
        lo, hi = min(vals), max(vals)
        return (lambda x: 0.0 if hi == lo else (x - lo) / (hi - lo))
    nv, nva, nsc = _norm("vorp"), _norm("vona"), _norm("cliff")

    for r in out:
        base = w["vorp"] * nv(r["vorp"]) + w["vona"] * nva(r["vona"]) + w["scarcity"] * nsc(r["cliff"])
        # urgency: a strong player unlikely to survive to your next pick is worth
        # taking now — tilt the score by how little of them is expected to remain.
        urgency = base * (1.0 + 0.5 * (1.0 - r["survival"]))
        # ...then discount streamable positions (IDP/K/DST) so they don't crowd the early board.
        r["score"] = round(urgency * STREAM_DISCOUNT.get(r["pos"], 1.0), 4)

    out.sort(key=lambda r: -r["score"])
    return out


# --------------------------------------------------------------------------- #
# Self-test (no league data needed) — `python models/value_engine.py`
# --------------------------------------------------------------------------- #
def _synthetic_players() -> list[dict]:
    """Fabricate a believable pool so the engine can be verified deterministically."""
    pool, adp = [], 1
    shape = {"QB": (28, 380, 7), "RB": (60, 360, 4), "WR": (70, 340, 3.5),
             "TE": (24, 260, 6), "K": (20, 160, 2), "DST": (20, 170, 2.5),
             "IDP": (40, 200, 2)}
    for pos, (n, top, drop) in shape.items():
        for i in range(n):
            pool.append({"name": f"{pos}{i+1}", "team": "FA", "position": pos,
                         "points": round(top - i * drop, 1), "adp": None})
    # assign a plausible ADP roughly by projected points
    for rank, p in enumerate(sorted(pool, key=lambda x: -x["points"]), start=1):
        p["adp"] = rank
        p["rank"] = rank
    return pool


if __name__ == "__main__":
    lg = League()
    players = _synthetic_players()
    levels = replacement_levels(players, lg)
    print("Replacement levels:", {k: round(v, 1) for k, v in levels.items()})
    print(f"picks_until_next(pick=7, 12 teams): {picks_until_next(7, 12)}  "
          f"(survival of ADP=30 there: {survival(30, 7 + picks_until_next(7, 12)):.2f})")
    board = rank_board(players, lg, overall_pick=7)
    print("\nTop 12 DraftIQ board @ pick 7:")
    print(f"{'#':>2} {'PLAYER':<7} {'POS':<4} {'PTS':>6} {'VORP':>6} {'VONA':>6} {'SURV':>5} {'SCORE':>6}")
    for i, r in enumerate(board[:12], 1):
        print(f"{i:>2} {r['name']:<7} {r['pos']:<4} {_pts(r):>6.1f} "
              f"{r['vorp']:>6.1f} {r['vona']:>6.1f} {r['survival']:>5.2f} {r['score']:>6.3f}")
