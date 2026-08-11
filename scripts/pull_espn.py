"""Pull ESPN fantasy league history into data/raw/espn/<year>/.

Pulls, per season: scoring + roster settings, the draft board, final standings,
end-of-season rosters, the weekly score grid, and (with --box) per-player weekly
box scores. Everything the draft model needs to calibrate to *this* league.

Usage (from repo root, in the venv):
    python scripts/pull_espn.py --years 2021 2022 2023 2024 2025
    python scripts/pull_espn.py --years 2023 --box        # also per-player weekly
    python scripts/pull_espn.py --years 2024 --verify     # just confirm creds work

Credentials are read from .env (ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID) or flags.
Public leagues need only the league id; private leagues need both cookies.

Built and verified against espn-api 0.46.0 (see CLAUDE.md / the data-engineer
subagent). Notes baked in from an adversarial review of the espn-api internals:
- espn-api's Settings.position_slot_counts mislabels slots, so we dump the raw
  slot-id-keyed counts and map them via POSITION_MAP ourselves.
- box_scores raises before 2019 and is throttled by ESPN; we guard + back off.
- owners come from league `members` (dicts with displayName); empty on old seasons.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

try:
    from espn_api.football import League
    from espn_api.football.constant import POSITION_MAP
    from espn_api.requests.espn_requests import ESPNUnknownError
except ImportError:
    sys.exit("espn-api not installed. Run: pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "raw" / "espn"
BENCH_SLOTS = {"BE", "IR", "Bench"}


# --------------------------------------------------------------------------- #
# Helpers — defensive against attributes missing on historical seasons.
# --------------------------------------------------------------------------- #
def g(obj, name, default=None):
    return getattr(obj, name, default)


def warn(msg: str) -> None:
    print(f"    ! WARN: {msg}", file=sys.stderr)


def owner_str(team) -> str:
    """Owners come from league members. In espn-api 0.46 they're member dicts;
    displayName is the reliably-populated key. Empty when ESPN returns no members
    (common on old seasons) — fall back to team name then abbrev upstream."""
    out = []
    for o in g(team, "owners") or []:
        if isinstance(o, dict):
            name = (
                o.get("displayName")
                or f"{o.get('firstName', '')} {o.get('lastName', '')}".strip()
                or o.get("id", "")
            )
            out.append(name)
        else:
            out.append(str(o))
    return "; ".join(p for p in out if p)


def normalize_cookies(s2, swid):
    """ESPN requires SWID wrapped in {braces}; cookies are often copied without."""
    s2 = (s2 or "").strip().strip('"').strip("'") or None
    swid = (swid or "").strip().strip('"').strip("'") or None
    if swid and not swid.startswith("{"):
        swid = "{" + swid.strip("{}") + "}"
    return s2, swid


def write_csv(rows: list[dict], path: Path, label: str, columns=None) -> int:
    if not rows:
        print(f"    - {label}: 0 rows (skipped)")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    if columns:  # stable, self-documenting column order
        df = df.reindex(columns=columns)
    df.to_csv(path, index=False)
    print(f"    - {label}: {len(rows)} rows -> {path.relative_to(ROOT)}")
    return len(rows)


# --------------------------------------------------------------------------- #
# Per-section extractors
# --------------------------------------------------------------------------- #
def dump_settings(league, out_dir: Path) -> None:
    s = league.settings
    # espn-api's position_slot_counts pairs the first N POSITION_MAP labels with
    # raw counts and so MISLABELS real (non-contiguous) slot ids. Pull the raw
    # slot-id-keyed counts and map them ourselves. This is the field the model
    # most needs correct (IDP/FLEX/DST/K starters).
    starting_slots, slot_counts_raw, note = {}, {}, None
    try:
        raw = league.espn_request.get_league()
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        slot_counts_raw = (
            (raw.get("settings", {}) or {}).get("rosterSettings", {}) or {}
        ).get("lineupSlotCounts", {}) or {}
        starting_slots = {
            POSITION_MAP.get(int(sid), str(sid)): cnt
            for sid, cnt in slot_counts_raw.items()
            if cnt
        }
    except Exception as e:  # fall back to the (possibly mislabeled) library value
        note = f"raw roster settings unavailable ({e}); position_slot_counts may be mislabeled"
        warn(note)

    # _raw_scoring_settings is the raw scoringSettings dict (with a scoringItems
    # list inside) — not a list of records. Pull scoringItems defensively.
    raw_scoring = g(s, "_raw_scoring_settings", {}) or {}
    items = raw_scoring.get("scoringItems", []) if isinstance(raw_scoring, dict) else (raw_scoring or [])
    scoring_by_stat = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        sid = it.get("statId")
        if sid is not None:
            ov = it.get("pointsOverrides")  # D/ST tiered points-allowed live here
            scoring_by_stat[str(sid)] = ov if ov else it.get("points", 0)

    payload = {
        "league_name": g(s, "name"),
        "team_count": g(s, "team_count"),
        "scoring_type": g(s, "scoring_type"),
        "reg_season_count": g(s, "reg_season_count"),
        "playoff_team_count": g(s, "playoff_team_count"),
        "playoff_matchup_period_length": g(s, "playoff_matchup_period_length"),
        "final_scoring_period": g(league, "finalScoringPeriod"),
        "faab": g(s, "faab"),
        "keeper_count": g(s, "keeper_count"),
        # Roster slots — authoritative:
        "starting_slots": starting_slots,                # slot label -> count (correct)
        "lineup_slot_counts_raw": slot_counts_raw,       # slot-id keyed (authoritative)
        "position_slot_counts_espnapi": g(s, "position_slot_counts"),  # may be mislabeled
        # Scoring — authoritative is raw; scoring_format labels are lossy for unknown stats:
        "scoring_by_stat_id": scoring_by_stat,           # statId -> points (or D/ST overrides)
        "scoring_format": g(s, "scoring_format"),
        "raw_scoring_settings": raw_scoring,
    }
    if note:
        payload["_note"] = note
    (out_dir / "settings.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"    - settings: starting_slots={starting_slots or 'RAW-UNAVAILABLE'}")


def dump_rosters(league, out_dir: Path) -> tuple[int, dict]:
    """End-of-season roster snapshot. Also returns playerId -> position map.
    NOTE: lineup_slot on the season roster is unreliable (use box_players.slot)."""
    cols = ["team_id", "team_name", "player_id", "player_name", "position",
            "pro_team", "total_points", "projected_total_points"]
    rows, pos_by_id = [], {}
    for t in g(league, "teams", []) or []:
        for p in g(t, "roster", []) or []:
            pid, pos = g(p, "playerId"), g(p, "position")
            if pid is not None and pos:
                pos_by_id[pid] = pos
            rows.append({
                "team_id": g(t, "team_id"),
                "team_name": g(t, "team_name"),
                "player_id": pid,
                "player_name": g(p, "name"),
                "position": pos,
                "pro_team": g(p, "proTeam"),
                "total_points": g(p, "total_points"),
                "projected_total_points": g(p, "projected_total_points"),
            })
    return write_csv(rows, out_dir / "rosters.csv", "rosters", cols), pos_by_id


def dump_draft(league, out_dir: Path, pos_by_id: dict) -> int:
    cols = ["overall_pick", "round_num", "round_pick", "team_id", "team_name",
            "owner", "player_id", "player_name", "position", "bid_amount", "keeper"]
    draft = g(league, "draft", []) or []
    rows, blanks = [], 0
    for i, pk in enumerate(draft, start=1):  # league.draft is in overall-pick order (snake/linear)
        team = g(pk, "team")
        pid = g(pk, "playerId")
        pos = pos_by_id.get(pid, "")
        if not pos:
            blanks += 1
        rows.append({
            "overall_pick": i,
            "round_num": g(pk, "round_num"),
            "round_pick": g(pk, "round_pick"),
            "team_id": g(team, "team_id"),
            "team_name": g(team, "team_name"),
            "owner": owner_str(team),
            "player_id": pid,
            "player_name": g(pk, "playerName"),
            "position": pos,           # best-effort; full resolution is a model-layer join
            "bid_amount": g(pk, "bid_amount"),
            "keeper": g(pk, "keeper_status"),
        })
    n = write_csv(rows, out_dir / "draft.csv", "draft", cols)
    if blanks:
        warn(f"draft: {blanks}/{len(rows)} picks have no position (dropped before season end) "
             f"— resolve via the player crosswalk in the model layer")
    return n


def dump_standings(league, out_dir: Path) -> int:
    cols = ["team_id", "team_name", "abbrev", "owner", "wins", "losses", "ties",
            "points_for", "points_against", "reg_season_standing", "final_standing",
            "division_id"]
    rows = []
    for t in g(league, "teams", []) or []:
        fs = g(t, "final_standing")  # 0 == season not finalized
        rows.append({
            "team_id": g(t, "team_id"),
            "team_name": g(t, "team_name"),
            "abbrev": g(t, "team_abbrev"),
            "owner": owner_str(t),
            "wins": g(t, "wins"),
            "losses": g(t, "losses"),
            "ties": g(t, "ties"),
            "points_for": g(t, "points_for"),
            "points_against": g(t, "points_against"),
            "reg_season_standing": g(t, "standing"),
            "final_standing": fs if fs else None,
            "division_id": g(t, "division_id"),
        })
    rows.sort(key=lambda r: (r["final_standing"] or 99, r["reg_season_standing"] or 99))
    n = write_csv(rows, out_dir / "standings.csv", "standings", cols)
    tc = g(league.settings, "team_count")
    if tc and n and n != tc:
        warn(f"standings: {n} teams but settings say {tc}")
    return n


def dump_weekly_scores(league, out_dir: Path) -> int:
    """Team-level weekly grid from Team.scores/schedule/outcomes (index 0 == week 1,
    equal length, includes playoffs). On bye weeks ESPN sets opponent to the team
    itself — flag those so head-to-head math isn't corrupted."""
    cols = ["week", "is_regular", "team_id", "team_name", "points",
            "opp_team_id", "opp_team_name", "is_bye", "outcome"]
    reg = g(league.settings, "reg_season_count", 14) or 14
    rows = []
    for t in g(league, "teams", []) or []:
        scores = g(t, "scores", []) or []
        sched = g(t, "schedule", []) or []
        outcomes = g(t, "outcomes", []) or []
        for w in range(len(scores)):
            opp = sched[w] if w < len(sched) else None
            opp_id = g(opp, "team_id")
            is_bye = opp_id is not None and opp_id == g(t, "team_id")
            rows.append({
                "week": w + 1,
                "is_regular": (w + 1) <= reg,
                "team_id": g(t, "team_id"),
                "team_name": g(t, "team_name"),
                "points": scores[w],
                "opp_team_id": None if is_bye else opp_id,
                "opp_team_name": None if is_bye else g(opp, "team_name"),
                "is_bye": is_bye,
                "outcome": outcomes[w] if w < len(outcomes) else None,
            })
    rows.sort(key=lambda r: (r["week"], r["team_id"] or 0))
    return write_csv(rows, out_dir / "weekly_scores.csv", "weekly_scores", cols)


def dump_box_players(league, out_dir: Path) -> int:
    """Per-player weekly scoring via box_scores(). Heavy (one call per week, each
    doing several sub-requests) and ESPN-throttled — we back off and bound the loop
    by finalScoringPeriod. Box scores are unavailable before 2019."""
    cols = ["week", "is_playoff", "team_id", "team_name", "player_id", "player_name",
            "position", "slot", "started", "points", "projected_points", "on_bye"]
    if (g(league, "year", 9999) or 9999) < 2019:
        print("    - box_players: skipped (ESPN box scores unavailable before 2019)")
        return 0
    reg = g(league.settings, "reg_season_count", 14) or 14
    last_week = g(league, "finalScoringPeriod", reg + 4) or (reg + 4)
    cache, rows, errors = {}, [], 0
    for week in range(1, int(last_week) + 1):
        games = None
        for attempt in range(3):
            try:
                games = league.box_scores(week, player_team_cache=cache)
                break
            except ESPNUnknownError:  # usually rate-limit / transient — back off + retry
                errors += 1
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                print(f"    - box week {week}: skipped ({e})")
                break
        if not games:
            continue
        for bs in games:
            for side in ("home", "away"):
                team = g(bs, f"{side}_team")
                if not hasattr(team, "team_id"):  # bye/empty side stays None or int
                    continue
                for bp in g(bs, f"{side}_lineup", []) or []:
                    slot = g(bp, "slot_position")
                    rows.append({
                        "week": week,
                        "is_playoff": g(bs, "is_playoff"),
                        "team_id": g(team, "team_id"),
                        "team_name": g(team, "team_name"),
                        "player_id": g(bp, "playerId"),
                        "player_name": g(bp, "name"),
                        "position": g(bp, "position"),
                        "slot": slot,
                        "started": (slot not in BENCH_SLOTS) if slot else None,
                        "points": g(bp, "points"),
                        "projected_points": g(bp, "projected_points"),
                        "on_bye": g(bp, "on_bye_week"),
                    })
        time.sleep(0.4)  # be gentle with ESPN
    if errors:
        warn(f"box_players: {errors} week-requests hit ESPN errors (likely rate-limit) — data may be partial")
    return write_csv(rows, out_dir / "box_players.csv", "box_players", cols)


def update_team_map(league, year: int, map_path: Path) -> None:
    """Cumulative ESPN team_name/owner map to help reconcile persona names later.
    NOTE: this records ESPN names+owners only; bridging to the persona names in
    data/raw/2023_draft.csv and the UI short names is a model-layer recon step
    (join the draft CSVs on normalized player names to recover persona->team_id)."""
    existing = {}
    if map_path.exists():
        try:
            existing = json.loads(map_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    for t in g(league, "teams", []) or []:
        name = g(t, "team_name")
        if not name:
            continue
        e = existing.setdefault(str(name), {"owner": owner_str(t), "team_id": g(t, "team_id"),
                                            "abbrev": g(t, "team_abbrev"), "years": []})
        if year not in e["years"]:
            e["years"] = sorted(e["years"] + [year])
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(existing, indent=2, default=str), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def load_league(league_id, year, s2, swid):
    return League(league_id=int(league_id), year=int(year), espn_s2=s2, swid=swid)


def pull_year(league_id, year, s2, swid, out_root: Path, with_box: bool) -> bool:
    print(f"\n[{year}] connecting to league {league_id}...")
    try:
        league = load_league(league_id, year, s2, swid)
    except Exception as e:
        hint = ""
        if "NoneType" in str(e) or "totalPoints" in str(e):
            hint = "  (season may be in progress — scores not yet final)"
        elif "401" in str(e) or "403" in str(e):
            hint = "  (auth — check ESPN_S2 / ESPN_SWID cookies)"
        print(f"[{year}] FAILED to load league: {e}{hint}")
        return False

    out_dir = out_root / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)

    dump_settings(league, out_dir)
    _, pos_by_id = dump_rosters(league, out_dir)   # rosters first -> position map for draft
    dump_draft(league, out_dir, pos_by_id)
    dump_standings(league, out_dir)
    dump_weekly_scores(league, out_dir)
    if with_box:
        dump_box_players(league, out_dir)
    update_team_map(league, int(year), ROOT / "data" / "team_name_map.json")
    print(f"[{year}] done -> {out_dir.relative_to(ROOT)}")
    return True


def verify(league_id, year, s2, swid) -> bool:
    print(f"[verify] connecting to league {league_id} ({year})...")
    try:
        lg = load_league(league_id, year, s2, swid)
    except Exception as e:
        print(f"[verify] FAILED: {e}")
        return False
    teams = g(lg, "teams", []) or []
    draft = g(lg, "draft", []) or []
    print(f"[verify] OK — league '{g(lg.settings, 'name', '?')}': "
          f"{len(teams)} teams, {len(draft)} draft picks, "
          f"scoring_type={g(lg.settings, 'scoring_type', '?')}")
    return True


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description="Pull ESPN fantasy league history.")
    ap.add_argument("--years", nargs="+", type=int, required=True)
    ap.add_argument("--league-id", default=os.getenv("ESPN_LEAGUE_ID"))
    ap.add_argument("--espn-s2", default=os.getenv("ESPN_S2"))
    ap.add_argument("--swid", default=os.getenv("ESPN_SWID"))
    ap.add_argument("--box", action="store_true", help="also pull per-player weekly box scores")
    ap.add_argument("--verify", action="store_true", help="just confirm creds + league load, no files")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    if not args.league_id:
        sys.exit("No league id. Set ESPN_LEAGUE_ID in .env or pass --league-id.")
    s2, swid = normalize_cookies(args.espn_s2, args.swid)
    if not (s2 and swid):
        print("Note: no cookies provided — only works if the league is PUBLIC.")

    if args.verify:
        ok = all(verify(args.league_id, y, s2, swid) for y in args.years)
        sys.exit(0 if ok else 1)

    if not args.box:
        print("Note: per-player weekly data (--box) is OFF. The model needs it for "
              "VORP/VONA calibration — add --box for full history.")

    out_root = Path(args.out)
    ok = sum(pull_year(args.league_id, y, s2, swid, out_root, args.box) for y in args.years)
    print(f"\nDone: {ok}/{len(args.years)} seasons pulled into {out_root.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
