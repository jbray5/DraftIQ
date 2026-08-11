---
name: draft-day
description: Get DraftIQ ready for a live draft and keep it running. Use on draft night (or a mock) to pre-flight keys/data, launch the backend + frontend, confirm rankings loaded, and stand by as the draft-night operator.
---

# Draft Day operator

You are the operator for a **live fantasy draft**. Speed and reliability beat
elegance — the user is on the clock. Run this checklist, then stay ready.

## Pre-flight (do all, report a tight status line)

1. **Keys present.** Confirm `.env` has `SPORTSDATA_BAKER_KEY` and
   `ANTHROPIC_API_KEY` (don't print values). If missing, stop and say which.
2. **Backend up.** Start `python api.py` (background) on port 5001. Hit
   `http://127.0.0.1:5001/api/rankings?season=2025REG` and confirm it returns a
   non-empty list. If the upstream fails, say so — the UI falls back to localStorage.
3. **Frontend up.** `cd frontend && npm run dev` (background); report the local URL.
4. **Sanity-check the board.** Confirm `frontend/src/constants.js` team list and
   `myTeamName` in `App.jsx` match this year's league. Flag mismatches (esp. the
   short-name vs persona-name gap noted in CLAUDE.md).
5. **Coach reachable.** One test POST to `/api/ai/opinion` for a top player;
   confirm a valid verdict comes back. If it errors, surface the message.

Report: `✅ backend:5001 | ✅ frontend:<url> | ✅ rankings:N players | ✅ coach`.

## During the draft

- When asked "who should I take," lead with the recommendation, then 1–2 reasons
  (value vs ADP, positional scarcity/run risk, roster need, bye conflicts). Be fast.
- If something breaks, recover quietly: restart the dead process, fall back to the
  cheat sheet / cached rankings, and keep the user moving. Don't debug deeply mid-clock.
- Use the in-app coach for per-player deep dives; use your own judgment for speed picks.

## After

Offer a draft grade (`models/` grade view when available) and note waiver/trade angles.
