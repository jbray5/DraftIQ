# DraftIQ

An intelligent, league-aware fantasy football draft command center — built to win
a specific 12-team ESPN half-PPR home league. DraftIQ combines live projections,
a calibrated draft model, and an AI coach (Claude) behind a fast drag-and-drop
draft board.

## Stack

- **Backend:** Python / Flask (`api.py`) — serves merged rankings, an AI coach
  endpoint, and league config.
- **Frontend:** React + Vite + Tailwind (`frontend/`) — drag-and-drop draft board,
  available-player pool, suggestions, your-team panel, undo/redo, localStorage.
- **AI coach:** Anthropic Claude (structured JSON advice per player).
- **Data:** SportsData.io (projections / ADP / headshots) + ESPN league history.

## Project layout

```
api.py                 Flask app: /api/rankings, /api/ai/opinion, /api/league-tendencies
context_builder.py     Prompt/context assembly for the coach
data/
  raw/                 Source data + league exports (drafts, projections, stats)
  processed/           Derived/cleaned tables (gitignored)
  league_tendencies.json   Per-team biases + scoring used by the coach
frontend/              React app (components/, hooks/, utils/, lib/)
models/                Draft model (VORP/VONA, scarcity, calibration) — WIP
.claude/               Claude Code config: skills, subagents, settings
CLAUDE.md              Guide for AI agents working in this repo
```

## Setup

```bash
# 1. Python backend
python -m venv .venv && . .venv/Scripts/activate   # Windows; use bin/activate on *nix
pip install -r requirements.txt
cp .env.example .env                                # then fill in keys

# 2. Run the API (port 5001)
python api.py

# 3. Frontend
cd frontend
npm install
npm run dev                                         # Vite dev server
```

## League

12-team ESPN, half-PPR, with IDP / K / D-ST. Roster: QB, RB1, RB2, WR1, WR2, TE,
FLEX, IDP, D/ST, K, + 7 bench. See `data/league_tendencies.json` and `CLAUDE.md`
for the model and scoring assumptions.

## Roadmap

- [x] Live rankings (SportsData.io) + drag-and-drop board
- [x] AI coach on Claude with structured output
- [ ] ESPN puller: live ADP + historical drafts/standings/weekly scores
- [ ] Calibrated draft model (VONA + positional scarcity + pick-timing + roster value)
- [ ] Cheat-sheet, draft-grade, and what-if/trade screens
