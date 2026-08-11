# DraftIQ model

The draft brain. Replaces the old "70% ADP" composite with a real value model.

## What's here

`value_engine.py` — pure, deterministic value engine:

- **VORP** — points over a **league-correct replacement level** (12 teams, 2RB/2WR/
  FLEX, and real baselines for IDP/K/D-ST, which most tools ignore).
- **VONA** — value over the best player likely to survive to **your next pick**
  (snake-aware: `picks_until_next` is verified across all picks/rounds).
- **survival** — P(player still on the board at your next pick), logistic on
  ADP vs the clock.
- **scarcity** — tier cliff at the position right now.
- These blend into one tunable **DraftIQ score**.

Run the self-test (no league data needed):

```bash
python models/value_engine.py
```

## Status: structure final, calibration pending

The *shape* of the model is done and tested. The *numbers* are not yet tuned to
this league — the self-test deliberately uses synthetic projections, so e.g. QBs
look over-valued there. Three calibration steps, all gated on the ESPN pull:

1. **League-scored projections.** Recompute each player's projected points under
   *this league's* scoring (`scoring_by_stat_id` from `data/raw/espn/<year>/settings.json`)
   from the component stats already in `data/raw/player_projections*.csv` — instead
   of SportsData's generic points. This is what makes IDP/D-ST/K values real.
2. **Real replacement levels & flex usage.** Derive `starters` and `flex_share`
   from `settings.json` (`starting_slots`) and observed lineup usage in
   `box_players.csv`, rather than the defaults in `League`.
3. **Tune weights + survival steepness via backtest.** Use the `backtest` skill:
   fit on held-out seasons, optimize the blend against what actually won this
   league (drafts → final standings / weekly scores). Never ship a weight change
   without a backtest.

## Then: integrate

- Serve the ranked board from `api.py` (extend `/api/rankings` or add `/api/board`)
  so the frontend Suggestions panel and the cheat-sheet view use DraftIQ score
  instead of the placeholder composite.
- Feed VORP/VONA/survival/scarcity into the AI coach context for sharper rationale.
