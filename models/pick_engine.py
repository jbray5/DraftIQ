"""DraftIQ "Optimal Pick Right Now" engine.

value_engine.rank_board is hardwired to overall_pick=1 at build time, so the
persisted board's survival/VONA reflect pick 1 only. This module re-scores the LIVE
remaining board at the current pick, then layers in the signals VORP alone can't
give late in a draft:

  * roster need        — an empty REQUIRED starter (e.g. TE) outranks bench depth
  * pick-survival      — will this player make it back to your next snake pick?
  * positional runs    — is a run draining this position right now?
  * tier cliffs        — is this the last startable player in their tier?
  * flatten rescue     — when VORP is ~equal for everyone (round 8+), switch the
                         fuel to ceiling / tier / run / opponent-need
  * streamability      — K/DST/IDP never surface early or as a 2nd one

It returns a NAMED shortlist (so the coach can name real alternatives), a
deterministic anchor (the single best pick now), per-player urgency, and the list
of teams picking before your next pick with their inferred needs.

Graceful by default: "who picks before me / what they need" is pure snake math on
the live pick log with ZERO manager-profile data. Profiles add an optional
confidence-weighted prior only once data/team_aliases.json resolves UI names.

Pure / deterministic. Run `python models/pick_engine.py` for a self-test.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

try:  # works both as `python models/pick_engine.py` and `from models import pick_engine`
    from value_engine import League, rank_board, picks_until_next, survival, normalize_pos
except ImportError:  # pragma: no cover
    from models.value_engine import League, rank_board, picks_until_next, survival, normalize_pos

ROOT = Path(__file__).resolve().parents[1]
_CFG = json.loads((ROOT / "data" / "league_config.json").read_text(encoding="utf-8"))
_SLOT2POS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "DP": "IDP", "D/ST": "DST", "K": "K"}

STREAM = {"IDP", "K", "DST"}
# bench depth has DIMINISHING returns, never a hard cap — a late-round 3rd QB flyer (upside
# stash or to block a QB-needy rival) stays legal, just heavily demoted so it's never the pick
# while real value exists. `keep` = copies that hold full bench value before decay kicks in;
# `decay` = the multiplier applied per extra copy beyond that.
DEPTH_KEEP = {"QB": 1, "TE": 1, "RB": 5, "WR": 5}
DEPTH_DECAY = {"QB": 0.18, "TE": 0.18, "RB": 0.5, "WR": 0.5}
TIER_SIZE = {"QB": 3, "RB": 6, "WR": 6, "TE": 3, "IDP": 6, "K": 8, "DST": 8}
# the live UI starting grid (DP slot == IDP); FLEX slots accept RB/WR/TE
ROSTER_SLOTS = ["QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "FLEX2", "IDP", "DST", "K"]
SLOT_BASE = {"QB": "QB", "RB1": "RB", "RB2": "RB", "WR1": "WR", "WR2": "WR", "TE": "TE",
             "FLEX": None, "FLEX2": None, "IDP": "IDP", "DST": "DST", "K": "K"}
FLEX_ELIG = ("RB", "WR", "TE")
DEDICATED_NEED = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "IDP": 1, "DST": 1, "K": 1}
# rough league-wide draft share per position, for run detection
BASE_SHARE = {"RB": 0.27, "WR": 0.31, "QB": 0.10, "TE": 0.10, "IDP": 0.10, "DST": 0.06, "K": 0.06}


def league_2026() -> League:
    c = _CFG["2026"]
    starters: dict[str, int] = {}
    for slot, n in c["starters"].items():
        b = _SLOT2POS.get(slot, slot)
        starters[b] = starters.get(b, 0) + n
    return League(teams=c["teams"], starters=starters, flex_slots=c["flex_slots"],
                  flex_eligible=tuple(c["flex_eligible"]), flex_share=c["flex_share"])


def tier_for(pos: str, pos_rank: int) -> int:
    return 1 + (max(1, int(pos_rank)) - 1) // TIER_SIZE.get(pos, 6)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pts(r) -> float:
    v = r.get("points")
    return float(v) if isinstance(v, (int, float)) else 0.0


def _norm_name(s) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _slot_for_pick(overall_pick: int, teams: int) -> int:
    r = (overall_pick - 1) // teams + 1
    q = (overall_pick - 1) % teams + 1
    return q if r % 2 == 1 else teams - q + 1


def roster_state(my_roster: dict) -> dict:
    """{openStarter:{pos:int}, flexOpen:int, filled:{pos:int}} from myRoster vs the grid."""
    filled: dict[str, int] = {}
    open_dedicated: dict[str, int] = {}
    flex_open = 0
    for slot in ROSTER_SLOTS:
        v = (my_roster or {}).get(slot)
        has = bool(v and (v.get("name") if isinstance(v, dict) else v))
        base = SLOT_BASE[slot]
        if base is None:  # FLEX
            if not has:
                flex_open += 1
            elif isinstance(v, dict):
                pos = normalize_pos(v.get("position"))
                if pos:
                    filled[pos] = filled.get(pos, 0) + 1
        elif has:
            filled[base] = filled.get(base, 0) + 1
        else:
            open_dedicated[base] = open_dedicated.get(base, 0) + 1
    return {"openStarter": open_dedicated, "flexOpen": flex_open, "filled": filled}


def open_needs(players: list[dict]) -> list[str]:
    """Which base starter positions a team still needs (template minus what they have)."""
    have: dict[str, int] = {}
    for p in players or []:
        pos = normalize_pos(p.get("position") or p.get("pos"))
        have[pos] = have.get(pos, 0) + 1
    return [pos for pos, n in DEDICATED_NEED.items() if have.get(pos, 0) < n]


def profile_prior(prof: dict | None, pos: str) -> float:
    """Confidence-weighted nudge to a manager's positional demand. 1.0 = neutral (unmapped,
    low-data, or no lean). Only high/medium-confidence profiles move the needle."""
    if not prof or prof.get("confidence") == "low":
        return 1.0
    if pos == "QB":
        q = prof.get("qb_first_round_avg")
        if q is None:
            return 1.0
        return 1.4 if q <= 4.1 else (0.6 if q >= 6.1 else 1.0)   # takes QB early / waits on QB (room avg ~5.1)
    if pos == "RB":
        return 1.3 if (prof.get("rb_in_first4") or 0) >= 2.2 else 1.0
    if pos == "WR":
        return 1.3 if (prof.get("wr_in_first4") or 0) >= 2.2 else 1.0
    return 1.0


def _profile_label(prof: dict | None) -> str:
    """Short human-readable tendency for the coach prompt."""
    if not prof:
        return "unmapped"
    if prof.get("confidence") == "low":
        return "new/low-data"
    bits = []
    q = prof.get("qb_first_round_avg")
    if q is not None and q <= 4.1:
        bits.append("takes QB early")
    elif q is not None and q >= 6.1:
        bits.append("waits on QB")
    if (prof.get("rb_in_first4") or 0) >= 2.2:
        bits.append("RB-heavy early")
    if (prof.get("wr_in_first4") or 0) >= 2.2:
        bits.append("WR-heavy early")
    return "; ".join(bits) if bits else "balanced"


def intervening_teams(overall_pick: int, teams: int, gap: int, team_order: list[str],
                      team_rosters: dict, my_slot: int | None = None,
                      profiles: dict | None = None, alias_map: dict | None = None) -> list[dict]:
    """Snake math: the teams picking before your next pick, each with inferred needs and a
    confidence-weighted manager tendency (when the UI name resolves via team_aliases.json)."""
    out = []
    for k in range(1, gap + 1):
        seat = _slot_for_pick(overall_pick + k, teams)
        if my_slot and seat == my_slot:   # skip your own back-to-back pick at the turn
            continue
        name = team_order[seat - 1] if team_order and seat - 1 < len(team_order) else f"Seat{seat}"
        needs = open_needs((team_rosters or {}).get(name, []))
        owner = (alias_map or {}).get(name)
        prof = None
        if owner and profiles:
            prof = profiles.get(owner) or profiles.get(str(owner).split(";")[0].strip())
        out.append({"team": name, "seat": seat, "needs": needs,
                    "profile": _profile_label(prof), "_prof": prof})
    return out


def run_heat(picks_log: list[dict], teams: int) -> dict:
    """Positional-run detection over the last ~2 rounds: observed share / baseline share."""
    window = (picks_log or [])[-2 * teams:]
    if not window:
        return {}
    c = Counter(normalize_pos(p.get("pos") or p.get("position")) for p in window)
    n = len(window)
    heat = {}
    for pos, cnt in c.items():
        base = BASE_SHARE.get(pos, 0.10)
        heat[pos] = round((cnt / n) / base, 2) if base else 0.0
    return heat


def shortlist(board_rows: list[dict], drafted_names, my_roster: dict, picks_log: list[dict],
              overall_pick: int, teams: int = 10, team_order: list[str] | None = None,
              team_rosters: dict | None = None, my_slot: int | None = None, rounds: int = 18,
              bench: list | None = None, profiles: dict | None = None,
              alias_map: dict | None = None, top: int = 12) -> dict:
    """Primary entry. Returns {shortlist, anchor, flat, intervening, meta}. Deterministic."""
    league = league_2026()
    drafted = {_norm_name(n) for n in (drafted_names or [])}
    pool = []
    for r in board_rows:
        if _norm_name(r.get("name")) in drafted:
            continue
        pool.append({**r, "points": _f(r.get("league_pts")) or _f(r.get("points")) or 0.0,
                     "position": r.get("pos") or r.get("position"), "adp": _f(r.get("adp"))})
    gap = picks_until_next(overall_pick, teams)
    next_pick = overall_pick + gap
    # at the snake turn you pick back-to-back, then wait — use the gap to the pick AFTER
    # next as the real scarcity horizon so urgency isn't understated at the turn.
    horizon = gap if gap >= 3 else gap + picks_until_next(next_pick, teams)
    meta = {"nextPick": next_pick, "picksUntilNext": gap,
            "round": (overall_pick - 1) // teams + 1}
    if not pool:
        return {"shortlist": [], "anchor": None, "flat": False, "intervening": [], "meta": meta}

    # Use the board's (full-pool, CORRECT) vorp + draft_value. Re-running rank_board on
    # only the remaining pool would recompute replacement against a depleted pool and
    # INFLATE vorp. We compute survival / scarcity / tiers live; value stays the board's.
    ranked = [{
        "name": p.get("name"), "team": p.get("team"),
        "pos": normalize_pos(p.get("position")), "points": p["points"],
        "adp": p.get("adp"), "vorp": _f(p.get("vorp")) or 0.0, "val": _f(p.get("draft_value")) or 0.0,
        "ecr": (int(_f(p.get("ecr"))) if _f(p.get("ecr")) else None),
        "sos": (int(_f(p.get("sos"))) if _f(p.get("sos")) else None),
    } for p in pool]

    pos_groups: dict[str, list] = {}
    for r in ranked:
        pos_groups.setdefault(r["pos"], []).append(r)
    pts_rank, adp_rank, cliff_by = {}, {}, {}
    for pos, lst in pos_groups.items():
        by_pts = sorted(lst, key=lambda x: -_pts(x))
        for i, r in enumerate(by_pts):
            pts_rank[id(r)] = i + 1
            nxt = _pts(by_pts[i + 1]) if i + 1 < len(by_pts) else _pts(r)
            cliff_by[id(r)] = round(_pts(r) - nxt, 2)        # drop to the next available at pos
        for i, r in enumerate(sorted(lst, key=lambda x: (x.get("adp") is None, x.get("adp") or 9999))):
            adp_rank[id(r)] = i + 1
    adp_queue = {}   # 0-indexed position in the GLOBAL ADP queue among AVAILABLE players
    for i, r in enumerate(sorted(ranked, key=lambda x: (x.get("adp") is None, x.get("adp") or 99999))):
        adp_queue[id(r)] = i

    rs = roster_state(my_roster)
    openStarter, flexOpen, filled = rs["openStarter"], rs["flexOpen"], rs["filled"]
    roster_count = dict(filled)                 # full position counts incl. bench (saturation)
    for b in (bench or []):
        bp = normalize_pos(b.get("position") or b.get("pos"))
        if bp:
            roster_count[bp] = roster_count.get(bp, 0) + 1
    skill_open = any(openStarter.get(p, 0) > 0 for p in ("QB", "RB", "WR", "TE")) or flexOpen > 0
    empty_stream = sum(1 for s in ("IDP", "DST", "K") if openStarter.get(s, 0) > 0)
    if my_slot:
        my_picks_left = sum(1 for p in range(overall_pick, rounds * teams + 1) if _slot_for_pick(p, teams) == my_slot)
    else:
        my_picks_left = max(1, rounds - meta["round"] + 1)
    stream_late = my_picks_left <= empty_stream   # prioritize streamers only when out of slack picks
    startable_depth: dict[str, int] = {}
    for r in ranked:
        if r["vorp"] > 0:
            startable_depth[r["pos"]] = startable_depth.get(r["pos"], 0) + 1

    interv = intervening_teams(overall_pick, teams, gap, team_order or [], team_rosters or {},
                               my_slot, profiles, alias_map)
    demand: dict[str, float] = {}
    for t in interv:
        prof = t.get("_prof")
        for pos in t["needs"]:                       # weight by each manager's tendency (1.0 if unmapped)
            demand[pos] = demand.get(pos, 0) + profile_prior(prof, pos)
    heat = run_heat(picks_log, teams)

    # IDP is market-timed, not streamed. The room's 7 draft histories put the first
    # IDP at pick 106-134 (mean 123) and the elite tier is ~5 deep, so the engine
    # holds IDP as "cheap later" — until the market window (best available IDP's
    # league-derived ADP, models/espn_proj.market_adp) closes on the trip to your
    # next pick. Then it acts like any scarce open starter.
    _idp_best_adp = min((r["adp"] for r in ranked
                         if r["pos"] == "IDP" and r.get("adp") is not None), default=None)
    idp_window_closing = (_idp_best_adp is not None
                          and (_idp_best_adp - overall_pick) <= horizon)

    def need_mult(pos: str) -> float:
        if pos in STREAM:
            if filled.get(pos, 0) >= 1:        # never a 2nd streamer
                return 0.0
            if skill_open:                     # fill skill starters first
                return 0.0
            if pos == "IDP" and idp_window_closing:
                return 1.5                     # market says now-or-never for the elite tier
            # demote streamers BELOW bench depth until your final picks — then force the
            # fill like any empty required starter (never leave K/DST/IDP unfilled)
            return 1.6 if stream_late else 0.45
        if openStarter.get(pos, 0) > 0:        # empty required starter beats depth
            m = 1.6
            if startable_depth.get(pos, 0) <= horizon:   # supply won't survive the round-trip
                m *= 1.25
            return m
        if pos in FLEX_ELIG and flexOpen > 0:
            # FLEX is an RB/WR slot in practice: 1,151 real started flexes 2019-2025
            # were 58.6% WR / 40.1% RB / 1.4% TE, and a TE4 in flex scores ~1-2 pts/wk
            # BELOW the RB20/WR20 you'd start instead. A 2nd TE rides below RB/WR
            # bench value — it must never headline while a flex is open.
            return 1.15 if pos in ("RB", "WR") else 0.5
        # pure bench depth — DIMINISHING returns (never a hard zero, so a late flyer/block stays
        # legal). QB/TE depth is worth far less than RB/WR depth and decays steeply past a backup.
        have = roster_count.get(pos, 0)
        base = 0.30 if pos in ("QB", "TE") else 0.55
        keep = DEPTH_KEEP.get(pos, 5)
        if have > keep:
            base *= DEPTH_DECAY.get(pos, 0.4) ** (have - keep)
        return base

    def base_survival(r) -> float:
        # conditional on being available NOW: over `horizon` more picks, ~the top `horizon`
        # available players (by ADP) get taken; you survive if your queue position is deeper.
        z = 0.4 * (adp_queue.get(id(r), len(ranked)) - horizon)
        if z > 30:
            return 1.0
        if z < -30:
            return 0.0
        return 1.0 / (1.0 + math.exp(-z))

    def adj_survival(r) -> float:
        base = base_survival(r)
        pos = r["pos"]
        pressure = demand.get(pos, 0) / max(1, gap)        # upcoming picks needing this pos
        rh = max(0.0, heat.get(pos, 1.0) - 1.0)            # live run heat above baseline
        factor = max(0.02, 1.0 - 0.5 * pressure - 0.12 * rh)
        return max(0.0, min(1.0, base * factor))

    def ceiling_proxy(r) -> float:    # market (ADP) ranks them above their raw points -> upside
        return (pts_rank.get(id(r), 99) - adp_rank.get(id(r), 99))

    def tier_cliff(r) -> float:   # only EARLY-tier cliffs matter; deep-tier "cliffs" are noise
        pr = pts_rank.get(id(r), 1)
        t = tier_for(r["pos"], pr)
        return 1.0 if (t <= 3 and t < tier_for(r["pos"], pr + 1)) else 0.0

    def run_term(pos: str) -> float:
        return max(0.0, min(1.0, heat.get(pos, 0.0) - 1.0))

    def need_fit(r) -> float:
        if openStarter.get(r["pos"], 0) > 0:
            # an empty K/DST/IDP slot earns full fill-credit only once it's actually time
            # to stream — until then a 100%-survival kicker must not outscore skill depth.
            if r["pos"] in STREAM and not stream_late:
                return 0.12
            return 1.0
        if r["pos"] in FLEX_ELIG and flexOpen > 0:
            return 0.5 if r["pos"] in ("RB", "WR") else 0.2   # TE ~never starts in flex here
        return 0.15

    def reach_penalty(r) -> float:
        # demote (NEVER hide) players drafted well before their ADP, scaled by how early —
        # keeps a deep scrub from anchoring while still surfacing real late-round flier options.
        a = r.get("adp")
        reach = (a - overall_pick) if a else 30
        return 1.0 if reach <= 20 else max(0.15, 1.0 - (reach - 20) / 120.0)

    # flatten detection over the need-eligible candidates
    elig = [r for r in ranked if need_mult(r["pos"]) > 0]
    top_vorps = sorted((r["vorp"] for r in elig), reverse=True)[:8]
    flat = (len(top_vorps) >= 2 and (top_vorps[0] - top_vorps[-1]) < 3.0)

    # A 2nd TE's board VORP is measured against TE replacement (TE11) — but with the
    # TE starter filled his only landing spot is FLEX, where the real alternative is
    # an RB/WR. Backtested 2019-2025: started flexes are 98.6% RB/WR, and TE4's
    # 11.4 pts/wk LOSES to the RB20/WR20 (12.6/12.2) you'd start instead. So a
    # flex-only TE's value is capped at the best available RB/WR's value — he can
    # ride the shortlist, never anchor over the flex alternative he'd displace.
    _rbwr_val_cap = max((r["val"] for r in ranked if r["pos"] in ("RB", "WR")), default=None)

    def _mk_norm(vals):
        if not vals:
            return lambda x: 0.0
        lo, hi = min(vals), max(vals)
        return (lambda x: 0.0 if hi == lo else (x - lo) / (hi - lo))
    # normalize value over the TOP band (not the whole eligible pool) so deep-negative bench
    # depth can't compress the top tier — keeps Bijan >> a tier-cliff TE at pick 1.
    _vals = sorted((r["val"] for r in elig), reverse=True)
    _hi = _vals[0] if _vals else 1.0
    _lo = _vals[min(len(_vals) - 1, 23)] if _vals else 0.0
    n_val = (lambda x: 0.0 if _hi <= _lo else max(0.0, min(1.0, (x - _lo) / (_hi - _lo))))
    n_ceil = _mk_norm([ceiling_proxy(r) for r in elig])

    for r in ranked:
        nm = need_mult(r["pos"])
        r["_nm"] = nm
        if nm <= 0:
            r["_pnv"] = -1.0
            continue
        adjs = adj_survival(r)
        # tier-cliff / ceiling are skill-position signals; for streamers they're artifacts
        # (a "tier 1 kicker cliff" is noise) — zero them so K/DST/IDP score on value+timing.
        tcb = 0.0 if r["pos"] in STREAM else tier_cliff(r)
        # A kicker/defense's preseason VORP is fiction — you stream the best matchup weekly,
        # so replacement ≈ equal and their DRAFT value is ~zero. Their pick is pure timing
        # (need_fit × needMult in the final rounds). IDP keeps its (modest) real value —
        # it's a required starter, but scoring is 2025 rules (no buff; commish-confirmed).
        val_used = r["val"]
        if (r["pos"] == "TE" and openStarter.get("TE", 0) == 0
                and _rbwr_val_cap is not None):
            val_used = min(val_used, _rbwr_val_cap)     # flex-only TE: see cap comment above
        val_term = 0.0 if r["pos"] in ("K", "DST") else n_val(val_used)
        if flat:    # VORP is noise → ceiling / tier-cliff / runs / survival drive the pick
            ceil_term = 0.0 if r["pos"] in STREAM else n_ceil(ceiling_proxy(r))
            pnv = (0.34 * val_term + 0.22 * ceil_term
                   + 0.15 * tcb + 0.17 * (1 - adjs) + 0.12 * run_term(r["pos"])
                   + (0.12 * need_fit(r) if r["pos"] in ("K", "DST") else 0.0))
        else:       # value-dominant: draft-VORP wins this league (validated +0.56)
            pnv = (0.70 * val_term + 0.10 * tcb
                   + 0.08 * (1 - adjs) + 0.12 * need_fit(r))
        pr = pts_rank.get(id(r), 1)
        # urgency (deterministic, server-authoritative)
        if r["pos"] not in STREAM and openStarter.get(r["pos"], 0) > 0 and startable_depth.get(r["pos"], 0) <= horizon:
            urg = "pick-now"   # streamers are never forced — their survival (≈1.0) decides → can-wait
        elif adjs < 0.10:
            urg = "pick-now"
        elif adjs < 0.35:
            urg = "lean-now"
        elif adjs >= 0.65:
            urg = "can-wait"
        else:
            urg = "lean-now"
        fits = (r["pos"] if openStarter.get(r["pos"], 0) > 0
                else ("FLEX" if (r["pos"] in FLEX_ELIG and flexOpen > 0) else "BENCH"))
        why = (f"fills your open {r['pos']} starter" if openStarter.get(r["pos"], 0) > 0
               else f"stream {r['pos']} late" if r["pos"] in STREAM
               else "FLEX/upside depth" if fits == "FLEX" else "bench/upside")
        r.update({
            "_pnv": round(pnv * nm * reach_penalty(r), 4), "_tier": tier_for(r["pos"], pr),
            "_adjS": round(adjs, 3), "_urg": urg, "_fits": fits, "_why": why,
        })

    # ---- shortlist selection: positionally diverse, not just top-N by score ----
    ranked_elig = sorted((r for r in ranked if r.get("_pnv", -1) >= 0), key=lambda r: -r["_pnv"])
    seen, picked = set(), []

    def _add(r):
        if r["name"] not in seen:
            seen.add(r["name"])
            picked.append(r)

    need_positions = [p for p in ("QB", "RB", "WR", "TE") if openStarter.get(p, 0) > 0]
    if flexOpen > 0:
        need_positions += [p for p in FLEX_ELIG if p not in need_positions]
    need_positions += [p for p in ("IDP", "DST", "K") if need_mult(p) > 0]   # eligible streamers
    for pos in need_positions:
        n = 4 if pos in ("RB", "WR") else 3
        for r in [x for x in ranked_elig if x["pos"] == pos][:n]:
            _add(r)
    for r in ranked_elig[:2]:   # global best-pick-now guard
        _add(r)
    for r in sorted(ranked_elig, key=lambda r: -r["vorp"])[:2]:   # elite value / fallers always surface
        _add(r)
    for pos in FLEX_ELIG:       # bench skill depth is ALWAYS on the menu (never K-by-forfeit)
        for r in [x for x in ranked_elig if x["pos"] == pos][:2]:
            _add(r)

    picked.sort(key=lambda r: -r["_pnv"])
    out, stream_n, bench_n = [], 0, 0
    for r in picked:
        if r["pos"] in STREAM:
            if stream_n >= 1:
                continue
            stream_n += 1
        if r["_nm"] == 0.55:
            if bench_n >= 3:
                continue
            bench_n += 1
        out.append({
            "name": r["name"], "position": r["pos"], "team": r.get("team"),
            "proj": round(_pts(r)), "adp": r.get("adp"), "vorp": round(r["vorp"], 1),
            "cliff": cliff_by.get(id(r)), "tier": r["_tier"], "survival": round(base_survival(r), 3),
            "adjSurvival": r["_adjS"], "needMult": r["_nm"], "fits": r["_fits"],
            "pickNowValue": r["_pnv"], "urgency": r["_urg"], "why": r["_why"],
            "ecr": r.get("ecr"), "sos": r.get("sos"),
        })
        if len(out) >= top:
            break

    # FantasyPros-style pick confidence: the top pick's % reflects how much it dominates the
    # field (a runaway #1 → high; a coin-flip → modest); the rest scale by relative value.
    if out:
        topv = out[0]["pickNowValue"] or 1e-9
        r2 = (out[1]["pickNowValue"] / topv) if (len(out) > 1 and topv > 0) else 0.0
        top_conf = max(55, min(95, round(60 + 35 * (1 - r2))))
        for c in out:
            c["confidence"] = (max(5, min(99, round(top_conf * (c["pickNowValue"] / topv))))
                               if topv > 0 else 50)

    meta["openStarters"] = [p for p in ("QB", "RB", "WR", "TE", "IDP", "DST", "K")
                            if openStarter.get(p, 0) > 0]
    meta["runs"] = [{"pos": p, "heat": h} for p, h in sorted(heat.items(), key=lambda x: -x[1]) if h >= 1.6]
    return {"shortlist": out, "anchor": (out[0]["name"] if out else None),
            "flat": flat, "intervening": interv, "meta": meta}


# --------------------------------------------------------------------------- #
# Self-test — `python models/pick_engine.py`
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import csv

    path = ROOT / "data" / "processed" / "board_2026.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    print(f"loaded {len(rows)} board rows\n")

    # Simulate a value-drained mid-draft: top ~78 names gone, my team has its skill
    # starters but an empty TE / IDP / DST / K (the exact hole that produced the A-grade bug).
    drafted = [r["name"] for r in rows[:78]]
    my_roster = {
        "QB": {"name": "Lamar Jackson", "position": "QB"},
        "RB1": {"name": "Bijan Robinson", "position": "RB"},
        "RB2": {"name": "Jeremiyah Love", "position": "RB"},
        "WR1": {"name": "Drake London", "position": "WR"},
        "WR2": {"name": "DeVonta Smith", "position": "WR"},
        "FLEX": {"name": "Malik Nabers", "position": "WR"},
        "FLEX2": {"name": "Luther Burden III", "position": "WR"},
        "TE": None, "IDP": None, "DST": None, "K": None,
    }
    teams, my_slot = 10, 1
    pick = 80  # round 8, JRay (slot 1) back on the clock
    team_order = ["JRay", "Lamb", "Nico", "Blake", "Spivey", "Flowers", "TGil", "Will", "D-Put", "Munford"]
    picks_log = [{"overall": i + 1, "name": rows[i]["name"], "pos": rows[i]["pos"]} for i in range(78)]

    res = shortlist(rows, set(drafted), my_roster, picks_log, pick,
                    teams=teams, team_order=team_order)
    print(f"pick {pick} (round {res['meta']['round']}) · picksUntilNext {res['meta']['picksUntilNext']}"
          f" · FLAT={res['flat']} · openStarters={res['meta']['openStarters']}")
    print(f"anchor (optimal pick now) -> {res['anchor']}\n")
    print(f"{'PLAYER':<22} {'POS':<4} {'TIER':>4} {'PROJ':>5} {'VORP':>6} {'ADP':>6} "
          f"{'SURV':>5} {'URGENCY':<9} FITS")
    for c in res["shortlist"]:
        print(f"{c['name'][:22]:<22} {c['position']:<4} {c['tier']:>4} {c['proj']:>5} "
              f"{c['vorp']:>6} {str(c['adp']):>6} {c['adjSurvival']:>5} {c['urgency']:<9} {c['fits']}  · {c['why']}")
    print("\nstreamers present:", [c["name"] for c in res["shortlist"] if c["position"] in STREAM] or "none (correct — skill starters still open)")
