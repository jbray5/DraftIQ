# CLAUDE.md — DraftIQ

Guide for AI agents working in this repo. Keep it current; it's loaded every session.

## What this is

DraftIQ is a **live fantasy football draft tool** for one specific league, plus the
modeling around it. The mission is concrete: **win the league.** Two deliverables —
(1) a fast, trustworthy draft-day UI, and (2) a draft model calibrated to *this*
league's scoring and opponents.

## The league (assume these unless told otherwise)

- **12 teams, ESPN, snake draft, half-PPR.**
- Roster slots: `QB, RB1, RB2, WR1, WR2, TE, FLEX, IDP, D/ST, K` + 7 bench (see
  `frontend/src/constants.js` → `ROSTER_SLOTS`).
- **IDP** (individual defensive players), **K**, and **D/ST** all score and start —
  unusual, and the model must value them, not ignore them.
- The user's team is **"JRay"**. Exact scoring values come from an ESPN export
  (`data/league_tendencies.json` holds what we know: `half_ppr: true`, `qb_premium: false`).
- **Team-name bridge (BUILT):** `data/team_aliases.json` maps each live UI short name
  (`JRay`, `Spivey`, `D-Put`…) → ESPN owner handle (which keys
  `data/processed/manager_profiles.json`). The 2026 ten: JRay=jbray5, Lamb=
  espnfan1709357526, Nico=cjhubauer, Blake=wbbollinger08 (NEW 2025 → low-confidence,
  auto-neutralized), Spivey=tcspivey8, Flowers=payton9218600, TGil=Gtgilbert88,
  Will=wdsx5 (takes QB early ~rd 3.2), D-Put=dcputman, Munford=Henry Munford (RB-heavy).
  Note: D-Put & Blake troll each other via team names ("Blake's Holographic Charizard" is
  Deston/dcputman; "Deston's Ride or Dies" is Blake/wbbollinger08) — don't be fooled by the
  team name. `pick_engine.shortlist` consumes both files to weight opponent positional
  demand (manager priors). `data/team_name_map.json` (persona-team → owner/team_id/year) is
  the underlying source if the map needs rebuilding.
- **2026 draft order (ANNOUNCED):** 1 TGil (Taylor Gilbert), 2 Blake (Blake Bollinger),
  3 Nico (CJ Hubauer), 4 D-Put (Deston Putman), 5 Lamb (Stephen Walker), 6 Flowers
  (Payton Wester), 7 Spivey (T Spivey), 8 Will (Will Street), 9 Munford (Henry "Mac"
  Munford), **10 JRay — THE WHEEL** (back-to-back picks 10+11, 30+31, …; 18-pick waits
  between pairs). Frontend `TEAMS`/`MY_SLOT=10` and `scripts/mock_sim.py` reflect it.

## Architecture

| Piece | Path | Notes |
|---|---|---|
| Flask API | `api.py` | `/api/board` (DraftIQ value board), `/api/ai/best-pick` (**fused Optimal-Pick coach — primary on-the-clock**), `/api/ai/chat` (**free-form mid-draft Q&A** — question + bounded history + live state → pick-engine shortlist/survival + best-avail-by-pos context → grounded prose answer; ASK COACH panel under the verdict card, terminal `ask <question>`), `/api/ai/opinion` (Claude deep-dive), `/api/ai/closing`, `/api/trade`, `/api/rankings` (legacy), `/api/league-tendencies`. Coach prompts share ONE `coach_system_blocks()`/`LEAGUE_INSIGHTS` (api.py) — keep facts current THERE; it previously drifted stale in 3 places. **Sim family (deterministic, no LLM):** `/api/sim/opponents` (GHOST DRAFT), `/api/odds` (Monte-Carlo title odds), `/api/spy` (opponent dossier + predicted picks), `/api/wheel-plan` (two-pick pair planner). Port **5001**. TTL cache. |
| Opponent AI | `models/opponent_ai.py` | The room, drafting itself: ADP window + rank-decayed weights + roster need + streamer timing + manager priors from `manager_profiles.json`. Powers GHOST/SPY/WHEEL/WAR GAMES. `python models/opponent_ai.py` self-tests (all rosters must end legal). |
| **Live sync** | `models/espn_sync.py` | **Draft-night auto-logging**: polls the real ESPN draft (same creds as pull_espn) and maps every pick → board name (normalized, position-aware, D/ST-aware) + UI team (owner displayName → team_aliases inverted). `/api/espn/draft`; `?year=2025` replays last year's 204 picks as the pipeline fixture (92% board match; only-departed-players unmatched; 100% of returning-owner teams mapped). League object cached 8s. |
| **Waiver wire (IN-SEASON)** | `models/waivers.py` | **The post-draft edge, operational** (league DNA: in-season streaming + waivers are the only repeatable owner skills). Live ESPN rosters + free agents (same auth as espn_proj), valued on ESPN's league-scored ROS projections; in-season replacement = the 5TH-best FA at the position (best-FA zeroes vsWire by construction). Sections: UPGRADES (cracks your optimal lineup, via inseason.waiver_targets, each with a drop suggestion), DEPTH ADDS, D/ST STREAM for the current week (week1_odds is week-parameterized; implied-opp-total, the validated +55/yr play), INJURY FLAGS (OUT/IR/susp), ROOM ACTIVITY (lg.recent_activity). `/api/waivers` (5-min cache, ?week=N ?force=1), ⇄ WIRE chip in the fbar + terminal `wire`/`waivers`, CLI `python models/waivers.py`. |
| War games | `models/tournament.py` | N complete drafts from the CURRENT state (pick-engine anchor drives JRay, opponent AI drives the room) → 400-season Monte Carlo each → title-odds distribution, which 4-round openings win, most-drafted players, best draft found. `/api/sim/tournament` (~1s/draft). |
| **ESPN IDP proj** | `models/espn_proj.py` | **IDP projections from ESPN**, already scored in this league's exact (position-scoped) rules — no re-derivation, no double-count risk. Replaces SportsData IDP, broken since 2026-07-28. Pulls per-position (a single big `free_agents` call silently truncates) and **keeps only players whose OWN position is IDP** — two-way players like Travis Hunter come back from a CB query listed WR and would enter the board as a skill player with an IDP-inflated projection (he ranked #1 overall before this guard). Cached 24h, stale-on-failure. |
| **Week-1 odds** | `models/week1_odds.py` | **Matchup context for the streaming slots**, from ESPN's FREE public API (no key). `dst_ranks()` orders all 32 defenses by *implied opponent total* = (O/U + team_spread)/2; `k_context()` gives kickers their implied OWN total. Cached to `data/processed/week1_odds_2026.json` (6h TTL, stale-on-failure). Feeds `/api/board` (`w1Rank/w1Opp/w1OppTotal/…`) and F9 preflight. **Team codes normalize to the board's JAX/WAS** — ESPN says WSH, some feeds say JAC; getting it wrong silently drops teams. |
| Season sim | `models/season_sim.py` | 1000-season Monte Carlo → title/playoff odds. Weekly σ (~21.7) measured from 7 yrs of real `weekly_scores.csv`; 14-wk season + 6-team playoff w/ top-2 byes (2026 settings.json). ~40ms/call. |
| Wheel planner | `models/wheel.py` | Slot-10 back-to-back turn logic: branches the pick engine on each top candidate to find true best PAIRS, then Monte-Carlos the 18-pick wait (×25 room sims) for real survival odds + expected best-at-position at the next turn. |
| Coach context | `context_builder.py` | Assembles league + roster + candidate into the coach prompt. |
| Headshots | `headshots.py` | Player images from **ESPN's public roster API** (free, no key, ~99% board coverage). SportsData's Headshots feed is NOT in our plan — it returns the literal string `"Scrambled"` — and its CDN path 404s. Sweeps 32 team rosters, caches to `data/processed/headshots_espn.json` (24h TTL, serves stale on network failure). `python headshots.py` self-tests. |
| **Live UI** | `frontend/index.html` | **The real app** — vanilla-JS retro-terminal (CRT). Globals (POOL/TEAMS/PICKS/ROSTER…), `renderRec` is the fused rec card, localStorage persistence, 18-round draft. Overnight features (all additive, deterministic backend): **GHOST DRAFT** (F5 cycles OFF→ON→FAST; opponents auto-pick), **TITLE ODDS** ticker in the status line (click → full table + canvas title-race chart), **THE WHEEL** panel (auto-appears at back-to-back picks), **SONAR** live run/tier/value ticker, **SPY** dossiers (click any opponent header on the draft board). Viz layer (night 2, all client-side): CRT **boot sequence** (once/session, click to skip), synthesized **terminal audio** (🔊 toggle in status line, persisted), **SUPPLY CURVES** canvas on the cheat sheet (remaining VORP by position, dashed = expected gone by your next pick, dots = tier cliffs), **grade hero tiles** (Monte-Carlo title/playoff/xWins), steal **particle bursts** / reach **glitch** / draft-complete **fireworks**, **bye-week heat strip** in the roster panel, headshots on board chips. Ops layer (night 3): **⇅ LIVE SYNC** (board header; polls `/api/espn/draft` every 8s and auto-logs real picks — matched via `draftPlayer`, unmatched bookkept in lockstep; mutex with GHOST; conflict-pauses if local log disagrees), **⚔ WAR GAMES** (Draft Lab; ×12 futures with title-odds histogram + winning openings), **command terminal** (`` ` `` or Ctrl+K: draft/ask/spy/find/ghost/sync/war/odds/clock/mute/goto), **real pick clock** (resets per pick, `clock N` to configure, klaxon at 10s on your pick), **F9 PREFLIGHT** (`/api/health`: board age, feed sanity, ESPN connect, coach key, profiles). `afterPickChanged()` is the single post-pick hook; `vizTip()`/`vizCtx()` are the shared chart plumbing. (The old React app under `frontend/src/` is legacy.) |
| Draft model | `models/` | The brain — `pick_engine` (live Optimal-Pick) + VORP/VONA/scoring/calibrate/backtest/profiles/build_board. See "The model" below. |
| Frontend metrics | `frontend/src/utils/players.js` | `calculateDraftMetrics` now **prefers server `vorp`/`draftScore`** from `/api/board`; legacy ADP composite is only a fallback. |
| League config | `data/league_tendencies.json` | Per-team biases + scoring, edited via `/api/league-tendencies`. |
| Historical data | `data/raw/` | **ESPN league history 2012-2025 (14 seasons)**: drafts/standings/rosters/weekly for all years; per-player box scores 2019+ only (ESPN API limit). Manager-profile TENDENCIES stay scoped to 2019+ (`opponent_profiles.TENDENCY_SINCE` — old-era behavior diluted real reads, e.g. Munford's RB-heavy); titles/finishes count lifetime. Plus 2023 draft board, 2023/24 projections, 2023 actuals, injuries. |

Data flow: SportsData.io (Baker projections + ADP) + ESPN (headshots) → merged in
`api.py` → `/api/rankings` → `useDraftData` normalizes + computes metrics →
board/grid/suggestions.

## Run it

```bash
# Backend (needs .env with SPORTSDATA_BAKER_KEY + ANTHROPIC_API_KEY)
python api.py                      # serves http://127.0.0.1:5001

# Frontend
cd frontend && npm install && npm run dev
```

Windows shell is **PowerShell**; a Bash tool is also available. Don't `cd` in compound
commands (permission prompts) — use absolute paths.

## The model (BUILT — calibrated on 7 seasons of league history)

Lives in `models/`. Pipeline (run `python models/<file>.py` from the venv):

- `value_engine.py` — VORP / VONA / snake-aware pick-survival / scarcity → DraftIQ
  score. `STREAM_DISCOUNT` pushes IDP/K/DST down (user wants them streamed).
- `pick_engine.py` — **the live "Optimal Pick Right Now" engine.** Re-scores the board
  at the CURRENT pick (build_board hardwires pick=1), and layers roster-need (empty
  required starter > bench depth), conditional ADP-queue survival w/ a turn-aware
  horizon, positional-run detection, tier-cliffs, and a **flatten-rescue** (when VORP
  is flat mid/late, switch to ceiling/role/tier/run) → a NAMED shortlist + anchor +
  per-player urgency. Uses the board's full-pool VORP (do NOT re-run rank_board on the
  remaining pool — it inflates VORP). Served via `/api/ai/best-pick`, where Claude picks
  THE player + named alternatives ONLY from the shortlist. `python models/pick_engine.py` self-tests.
- `scoring.py` — SportsData component stats → points in league rules (validated
  corr 0.94 vs ESPN box).
- `calibrate.py` → `data/processed/player_seasons.csv`, `replacement_levels.json`.
- `backtest.py` — proved **draft-VORP wins this league** (corr +0.56 w/ points-for;
  raw points +0.26; positional templates ~0). QB can wait; IDP/K/DST stream.
- `opponent_profiles.py` → `data/processed/manager_profiles.json`, keyed by PERSON
  with a confidence flag (teams change managers — see gotchas).
- `dst.py` — **team D/ST projections.** SportsData's player feed has no defenses, so
  the board had none in a league that starts one (the UI faked 32 client-side). Uses
  `FantasyDefenseProjectionsBySeason` (in-plan) to ORDER the 32 defenses in league
  rules, then sets the SCALE from 7 seasons of ESPN box scores, shrunk by measured
  persistence. **Key finding: D/ST is not draftable skill.** Season-total persistence
  looks like +0.47 but that's an artifact (totals correlate +0.87 with weeks-rostered);
  on points-per-week it's +0.08–0.19, and only 0.83 of 3 top-3 defenses repeat. The raw
  DST1−DST11 gap of 77 pts/season shrinks to ~10. Top D/ST lands ~#80 overall. Stream it.
- `build_board.py` — fetches live SportsData projections → scores → values → appends
  D/ST → `data/processed/board_2026.csv` (the cheat sheet). **Run this to refresh the
  board.** Flags: `--reuse` keeps the existing board's player projections and only
  refreshes D/ST + re-ranks (use when upstream is degraded); `--force` overrides the
  feed sanity check. **It aborts by default on a broken feed** — on 2026-07-28 the IDP
  projections were half-populated (every starting LB at 0.0 tackles, league-wide max
  0.7 sacks, camp bodies carrying the only numbers); rebuilding blindly cut the board
  1720 → 1009 rows and gutted IDP. If it aborts, use `--reuse`, don't `--force`.

Structure (10-team/2-FLEX 2026 setup) is in `data/league_config.json`. **Always
backtest weight/replacement changes against history before trusting them**, and back
claims with the league's own data, not generic fantasy heuristics.

**Streaming is where D/ST value actually lives (measured 2026-08-09, 7 seasons).**
D/ST week-to-week persistence is **+0.001** — the unit you draft is worth ~10 pts of
season VORP, so *identity is nearly irrelevant*. But the **weekly matchup is strongly
predictive**: implied opponent total correlates **−0.294** with D/ST points, monotone
9.56 → 4.08 pts/wk across quintiles, and streaming on it beats a coin flip by **+55
pts/season** (vs +43 for ESPN's own projection; ceiling is +196). Home/away is noise
(+0.57, t=1.61). So the board now orders D/ST by **week-1 matchup**, not VORP.
**Kickers are not the same story** — best feature (implied own total) is r=+0.114 and
only +6 pts/season, so K shows the number as context and is never ranked by it.
⚠️ **SportsData cannot supply this**: 401 for 2019-2024, and its 2025 responses are
silently CORRUPTED (its own two endpoints disagree on the same game; every O/U that
week reads ~20 vs a real ~44). Use ESPN's free API. Beware ESPN providers named
`* - Live Odds` (they leak the result) and DraftKings' 2019 lines (contaminated).

**✅ TACKLE SCORING RESOLVED — commish ruling 2026-08-11: 1 pt per tackle FLAT.**
The saga, kept because each step is a trap someone will hit again:
(1) ESPN scoring is **position-scoped** via `pointsOverrides`, and `settings.json →
scoring_format` FLATTENS that away. `load_scoring(year, slot=)` reads the raw
`scoringItems`: base values for offense/IDP, `slot=DST_SLOT (16)` for the D/ST column
(tackles/PD zeroed, sacks 1.0 not 2.0). **Never re-add a blanket scoring override.**
(2) The 2026 config initially paid Total 1.0 + Solo 1.5 + Asst 0.75 simultaneously, and
**tackles STACK** — verified against ESPN's own engine 2026-08-10 by reproducing
Cashman's appliedTotal (411.71) from raw projected counts to within 0.83 pts (the
no-stack hypothesis misses by 182). One solo tackle = 2.5 pts; classic ESPN IDP
double-pay misconfiguration. (3) User confirmed with the commish it WAS a
misconfiguration; commish removed the solo/asst items same day. **Live config now:
SK 2, TK 1 flat, PD 1.5, INT/FR/FF/SF/BLKK 2.** Repriced 2026-08-11: Cashman 412 → 200
(~11.8/wk), board stays LB-dominated, mock sims still take the first IDP in rd 9 — the
market-window strategy was invariant to the outcome, as predicted. The pipeline
re-reads the live config on every rebuild, so any future commish change reprices
automatically (`draft_morning.py` stage 2 diffs it loudly).

**IDP is MARKET-TIMED, not value-discounted (2026-08-09, user's framing).** An elite
IDP would be held all year (weekly persistence 0.158 ≈ TE), so streaming logic doesn't
apply — but the ROOM doesn't pay for IDPs early, so the play is to wait. Measured on
14 drafts: first IDP off the board rd 9.1 on average (recent era: picks 106-134, mean
123); the season's top IDP went pick 115-185 or UNDRAFTED; **0 of 14 champions took an
IDP by rd 8**; and the elite tier is ~5 deep, so even the old early-IDP era (2012-16,
first IDP rds 5-7) never drained it before pick ~105. Implementation: ESPN IDP rows get
a league-derived **market ADP** (`espn_proj.market_adp`, IDP1≈95 — kept ~1 rd early as
a cheap hedge even after the 8/11 buff removal) which flows through survival/MKT/opponent-AI naturally, plus a
pick_engine window trigger (best-IDP ADP within the horizon → need_mult 1.5). Validated:
mock drafts take Cashman at the 90/91 wheel pair, right before the window. STREAM_DISCOUNT
0.35 stays as a *display* rank; timing comes from the market model.

**14-season league DNA (2012-2025, 170 team-seasons):** the league's character is
stable — within-year corr(points-for, finish) is **−0.55 in BOTH eras**; champions'
first QB is rd **6.1** across all 14 years (QB-wait holds in every era); early-RB mix
never predicted finish; draft slot doesn't matter in the modern era (−0.02).

One calibration landed 2026-07-29 (backtested):
- **TE almost never flexes here.** 1,151 real started FLEX slots 2019-2025: 58.6% WR /
  40.1% RB / **1.4% TE**, and TE4's 11.4 pts/wk loses to the RB20/WR20 alternative
  (12.6/12.2). `flex_share` is now the measured {RB .42, WR .55, TE .03}, and
  `pick_engine` caps a flex-only TE's value at the best available RB/WR (a 2nd TE can
  ride the shortlist but never anchor over the flex alternative). This killed a real
  bug where the engine funneled 3 TEs into TE+FLEX+FLEX2.

Serving: `/api/board` returns the DraftIQ board (league points + VORP + DraftIQ
score) with headshots; the frontend prefers these server values over the legacy
client-side `calculateDraftMetrics` composite. The coach (`/api/ai/opinion`) gets the
validated insights + `manager_profiles.json` (weighted by confidence) injected.

**Late-round upside (backtested 2026-08-11, 2023+2024 proj-vs-actuals).** Projection
order is the BEST primary dart ranker (top-20 darts by projection hit startable 35-50%
vs 25% for market-premium order) — never re-rank darts by upside. But WITHIN the
comparable-projection band, market premium (FP ADP earlier than our rank) added +12pts
pooled hit rate (42% vs 19% in 2024 — McConkey/Worthy; flat in 2023), so it's a
TIEBREAKER: the **UPS column** (display-only, dart-zone skill players, sortable) =
board rank − fp_adp/ecr. `pick_engine.ceiling_proxy` now ranks market off
fp_adp→ecr→adp (the raw `adp` column is junk-compressed for skill; it still feeds
survival — a bigger surgery deliberately NOT done pre-draft). FP's upside/bust tags
don't export (placeholder text), but their quantitative core does: the consensus-
rankings export's BEST/WORST/STD.DEV columns (fp_blend.load_expert_spread, auto-
detected by header) → fp_best/fp_worst/fp_std board columns → ▲ ceiling / ⚠ bust
flags on UPS + coach context. Coach persona now classifies picks (SAFE FLOOR /
CEILING DART / HANDCUFF / STREAMER) and gives price-conditioned dart verdicts.

## AI coach

`/api/ai/opinion` → Anthropic Claude, **structured JSON** output
(`verdict / fitScore / rationale / pros / cons / risks / suggestedAlternatives`).
Default model `claude-opus-4-8`, override via `ANTHROPIC_MODEL`. Prompt-cache the
stable league context so repeated picks are cheap. When building/altering anything
Claude-API, consult the `claude-api` skill — don't hand-write from memory.

## Conventions & gotchas

- **Frontend:** match surrounding style (functional components, hooks, Tailwind
  utility classes, position color classes via `toPosClass`). No new state libs.
- **Secrets:** never commit `.env`. A GitHub PAT was once found in `.git/config`'s
  remote URL — keep credentials out of git config.
- **Two ID worlds:** SportsData merges by `PlayerID`/name+team; ESPN uses its own
  player ids; league CSVs use plain names. Normalize carefully when joining.
- **Don't trust the composite score as truth** — it's a placeholder until the real
  model lands.

## Where to look first

- Live draft UX → `frontend/src/App.jsx` + `components/`.
- Rankings/merge → `api.py` `rankings()`.
- Coach → `api.py` `ai_opinion()` + `context_builder.py`.
- Model/metrics → `frontend/src/utils/players.js` (and future `models/`).
