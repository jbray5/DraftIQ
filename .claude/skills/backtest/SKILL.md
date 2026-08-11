---
name: backtest
description: Evaluate a draft-model change against the league's real history before trusting it. Use whenever you alter scoring weights, replacement levels, VORP/VONA, or scarcity logic — to check it would have produced better/realistic results on past drafts and standings.
---

# Backtest the model

Never ship a model change on vibes. Validate it against *this league's* history.

## What "good" means here

The model exists to win **this** 12-team half-PPR (IDP/K/DST) league — not to match
generic rankings. So judge it on league-relevant questions:

1. **Hindsight value:** using a season's actual end-of-year points, would the model's
   board have beaten the order the room actually drafted in (`data/raw/2023_draft.csv`
   + future ESPN exports)? Compare total starting-lineup points the model's picks
   would have accrued vs. what teams really got.
2. **Replacement realism:** do the position replacement levels match where production
   actually fell off that year for this roster shape?
3. **Pick-timing realism:** does the availability model match when players actually
   went in this room (reaches/runs)?
4. **Standings correlation:** do model-projected team strengths correlate with actual
   final standings / weekly scores?

## How to run

- Backtest harness lives in `models/backtest.py` (build it if absent — keep it pure
  and deterministic so results are reproducible).
- Hold out a season, fit on the rest, report the metrics above plus a short diff vs
  the previous model version. Note any position where it regressed.
- ⚠️ Joining history needs the team-name map and player-id reconciliation (CLAUDE.md).

Report a verdict: ship / don't ship, with the numbers that justify it.
