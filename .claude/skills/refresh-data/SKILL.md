---
name: refresh-data
description: Pull fresh fantasy data and rebuild rankings. Use to refresh SportsData.io projections/ADP/headshots for a season, ingest an ESPN league export (drafts/standings/weekly scores), or regenerate the cheat sheet before a draft.
---

# Refresh data

Keep the player pool and league history current.

## Live projections / ADP (SportsData.io)

- Source of truth is `api.py` (`_fetch_baker`, `_fetch_adp_and_points`,
  `_fetch_headshots`), merged in `rankings()`. The frontend pulls
  `/api/rankings?season=<SEASON>` (e.g. `2025REG`).
- To force-refresh: the API caches with a TTL (`TTL_SECONDS`); restart the backend
  or wait out the TTL. Confirm counts look sane (≈ a few hundred ranked players).

## ESPN league history (drafts, standings, weekly scores)

- Use the ESPN puller (`scripts/pull_espn.py` when present; build it if not — see
  the `data-engineer` subagent). Needs `ESPN_LEAGUE_ID` and, for a private league,
  `ESPN_S2` + `ESPN_SWID` cookies from `.env`.
- Write raw pulls to `data/raw/espn/<year>/` and derived tables to `data/processed/`.
- Reconcile ESPN player ids and team names against the existing CSVs and the UI
  short-name map (see CLAUDE.md — the name map is the usual snag).

## Rebuild rankings / cheat sheet

- Re-run the model (`models/` when present) to regenerate the tiered board with
  VORP/VONA, scarcity, and league-correct replacement levels.
- Always eyeball the top of each position for obvious errors before trusting output.

Report what was pulled, row counts, and anything that didn't reconcile.
