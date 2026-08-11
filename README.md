# DraftIQ

An intelligent, league-aware fantasy football draft command center — built to win
a specific ESPN half-PPR home league (10 teams in 2026, with IDP / K / D-ST).
Live draft ops (auto-synced picks, ghost mocks, Monte-Carlo title odds, war games),
a draft model calibrated on 14 seasons of the league's own history, and an AI coach.

## ⚡ DRAFT DAY QUICKSTART (laptop)

```bash
git clone https://github.com/jbray5/draftiq.git && cd draftiq

# 1. Python backend (3.12+)
python -m venv .venv
. .venv/Scripts/activate          # Windows Git Bash; .venv\Scripts\Activate.ps1 in PowerShell
pip install -r requirements.txt

# 2. Secrets — copy .env from the desktop (NEVER in git). Keys needed:
#    SPORTSDATA_BAKER_KEY, ANTHROPIC_API_KEY,
#    ESPN_LEAGUE_ID, ESPN_S2, ESPN_SWID          <- ESPN cookies power LIVE SYNC

# 3. Refresh everything to draft-morning state (board, IDP/K from ESPN,
#    week-1 Vegas lines, headshots). Takes ~1 min:
python models/build_board.py

# 4. Run it
python api.py                      # backend :5001 (leave running)
cd frontend && npm install && npm run dev    # app at http://localhost:5173

# 5. In the app: press F9 (PREFLIGHT) — every check must be green.
#    Then flip ⇅ SYNC: LIVE on the draft board and draft when the chime sounds.
```

A committed **draft-day snapshot** of the processed board/caches means the app
works immediately after clone + `.env`, even if a data feed dies Saturday —
`build_board.py` just makes it fresher.

## Stack

- **Backend:** Python / Flask (`api.py`) — board, Optimal-Pick coach, live ESPN
  draft sync, opponent/ghost simulation, Monte-Carlo season odds, health checks.
- **Frontend:** single-file vanilla JS app (`frontend/index.html`) served by Vite —
  three themes (dark / light / original CRT terminal), canvas charts, command
  terminal (`` ` ``), sortable tables. (The React app under `frontend/src/` is legacy.)
- **AI coach:** Anthropic Claude (structured JSON; deterministic fallbacks everywhere).
- **Data:** SportsData.io (skill projections) · **ESPN** (IDP + K projections in
  league scoring, live draft, week-1 Vegas lines, headshots) · 14 seasons of the
  league's own exports in `data/raw/espn/`.

## Project layout

```
api.py                  Flask app — all endpoints (see CLAUDE.md for the full table)
models/                 The brain: pick_engine, value_engine, dst, espn_proj,
                        espn_sync, week1_odds, opponent_ai, season_sim, wheel,
                        tournament, scoring, calibrate, backtest, build_board
scripts/                pull_espn.py (league history), mock_sim.py, league_dna.py
data/raw/espn/<year>/   2012-2025 league exports (drafts, standings, rosters, box)
data/processed/         Derived tables (mostly gitignored; draft-day snapshot committed)
frontend/index.html     The real app (vanilla JS, single file)
.claude/                Claude Code config: skills, subagents
CLAUDE.md               Guide for AI agents — read this first
```

## The model, in one paragraph

Value is VORP under the league's exact position-scoped scoring, validated against
14 seasons of league history (draft-VORP correlates +0.56 with points-for; QB waits
— champions take their first QB ~round 6 in every era). Streamable positions are
market-timed rather than value-chased: D/ST is ordered by the week-1 Vegas matchup
(implied opponent total, r = −0.29), elite IDP is held until the room's historical
buying window (~pick 95+) closes, and kickers are a final-round pick on a good
offense. The room itself is simulated — each opponent drafts by their measured
tendencies — which powers ghost mocks, survival forecasts, and title odds.
