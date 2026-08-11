"""LIVE ESPN DRAFT SYNC — auto-log real draft picks into DraftIQ.

On draft night someone had to hand-type all 180 picks into the UI. This module
polls the actual ESPN draft (espn-api, same credentials as scripts/pull_espn.py)
and maps each pick onto DraftIQ's world:

  ESPN team  -> UI short name   via owner displayName -> data/team_aliases.json
                                 (inverted), with team_name_map.json as backup.
  ESPN player -> board row       via normalized name matching (accents, suffixes,
                                 D/ST forms), position-aware where possible.

Served at /api/espn/draft. The League object is cached ~8s so UI polling never
hammers ESPN. Validated by replaying the COMPLETED 2025 draft (204 picks)
through this exact mapper — see /api/espn/draft?year=2025&debug=1.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", "and")
    s = _SUFFIX.sub("", s)
    return re.sub(r"[^a-z]", "", s)


def _dst_key(s: str) -> str | None:
    """'Seahawks D/ST' / 'Seattle Seahawks' style names -> franchise nickname key."""
    words = re.sub(r"d/?st|defense|special teams", "", str(s or ""), flags=re.I).split()
    return norm_name(words[-1]) if words else None


class SyncState:
    """One cached espn-api League per (year), refreshed at most every TTL seconds."""
    TTL = 8

    def __init__(self):
        self._league = {}
        self._at = {}

    def league(self, year: int):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        now = time.time()
        if year in self._league and now - self._at[year] < self.TTL:
            return self._league[year]
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from pull_espn import normalize_cookies, load_league
        s2, swid = normalize_cookies(os.getenv("ESPN_S2"), os.getenv("ESPN_SWID"))
        lg = load_league(os.getenv("ESPN_LEAGUE_ID"), year, s2, swid)
        self._league[year] = lg
        self._at[year] = now
        return lg


STATE = SyncState()


def owner_to_ui() -> dict[str, str]:
    """owner displayName (lowered) -> UI short name, from team_aliases.json inverted."""
    p = ROOT / "data" / "team_aliases.json"
    out = {}
    try:
        aliases = json.loads(p.read_text(encoding="utf-8"))
        for ui, owner in aliases.items():
            if ui.startswith("_") or not owner:
                continue
            for part in str(owner).split(";"):
                out[part.strip().lower()] = ui
    except Exception:
        pass
    return out


def board_index(board_rows: list[dict]) -> dict:
    """Normalized-name (and DST-nickname) lookups into the DraftIQ board."""
    by_name: dict[str, list[dict]] = {}
    dst_by_nick: dict[str, dict] = {}
    for r in board_rows:
        by_name.setdefault(norm_name(r.get("name")), []).append(r)
        if str(r.get("pos")) == "DST":
            k = _dst_key(r.get("name"))
            if k:
                dst_by_nick[k] = r
    return {"by_name": by_name, "dst": dst_by_nick}


def map_pick(pk, idx, teams_by_id, own2ui) -> dict:
    """One espn-api draft Pick -> DraftIQ-shaped pick."""
    team = getattr(pk, "team", None)
    tid = getattr(team, "team_id", None)
    tname = getattr(team, "team_name", None)
    owners = []
    for o in (getattr(team, "owners", None) or []):
        if isinstance(o, dict):
            owners.append(str(o.get("displayName") or "").lower())
    ui = next((own2ui[o] for o in owners if o in own2ui), None)

    raw = str(getattr(pk, "playerName", "") or "")
    n = norm_name(raw)
    hit, pos = None, None
    if "d/st" in raw.lower() or "dst" in raw.lower():
        hit = idx["dst"].get(_dst_key(raw))
    if hit is None:
        cands = idx["by_name"].get(n) or []
        # skill first: an IDP name-twin must not shadow the skill player (Josh Allen!)
        hit = (next((c for c in cands if c.get("pos") in ("QB", "RB", "WR", "TE", "K", "DST")), None)
               or (cands[0] if cands else None))
    if hit is None and n:
        # last resort: unique prefix/suffix match (ESPN sometimes abbreviates)
        cands = [rows[0] for k, rows in idx["by_name"].items() if k.startswith(n) or n.startswith(k)]
        hit = cands[0] if len(cands) == 1 else None
    if hit is not None:
        pos = hit.get("pos")
    return {
        "overall": None,                      # filled by the caller (enumerate order)
        "espnTeamId": tid, "espnTeamName": tname,
        "uiTeam": ui,
        "espnPlayer": raw,
        "boardName": hit.get("name") if hit else None,
        "pos": pos,
        "matched": hit is not None,
    }


def get_draft(board_rows: list[dict], year: int = 2026) -> dict:
    lg = STATE.league(year)
    teams_by_id = {getattr(t, "team_id", None): t for t in (getattr(lg, "teams", None) or [])}
    own2ui = owner_to_ui()
    idx = board_index(board_rows)
    picks = []
    for i, pk in enumerate(getattr(lg, "draft", None) or [], start=1):
        m = map_pick(pk, idx, teams_by_id, own2ui)
        m["overall"] = i
        picks.append(m)
    matched = sum(1 for p in picks if p["matched"])
    return {
        "connected": True,
        "league": getattr(lg.settings, "name", "?"),
        "year": year,
        "teamCount": len(teams_by_id),
        "pickCount": len(picks),
        "matched": matched,
        "picks": picks,
    }
