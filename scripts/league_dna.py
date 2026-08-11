"""League DNA — what attributes WIN this league, year in year out?

Builds a per-team-season attribute table (2019-2025) across three phases:
  DRAFT     value accumulated, where in the draft, concentration, position timing
  IN-SEASON start/sit efficiency, waiver production, streaming intensity, consistency
  LUCK      schedule luck (all-play), close games, points-against

...then reports (1) which attributes correlate with finishing well, (2) which are
REPEATABLE owner skills vs one-off luck (year-over-year autocorrelation), and
(3) the champion profile + JRay's profile against it.

Output: data/processed/league_dna.csv + a printed report.
Run: python scripts/league_dna.py
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "models"))
RAW = ROOT / "data" / "raw" / "espn"
PROC = ROOT / "data" / "processed"
REPL = json.loads((PROC / "replacement_levels.json").read_text(encoding="utf-8"))["historical"]

from inseason import CFG, optimal_lineup, slot_spec  # noqa: E402


def bucket(pos: str) -> str:
    p = str(pos).upper().replace(".", "").replace(" ", "").replace("/", "")
    if p in ("RB", "WR", "QB", "TE"):
        return p
    if p in ("K", "PK"):
        return "K"
    if p in ("DST", "DEF", "D"):
        return "DST"
    if p in ("", "NAN", "NONE"):
        return ""
    return "IDP"


def years() -> list[int]:
    return sorted(int(os.path.basename(d)) for d in glob.glob(str(RAW / "2*"))
                  if os.path.isdir(d) and int(os.path.basename(d)) <= 2025)


def load_year(y: int) -> dict:
    d = {}
    for name in ("draft", "standings", "weekly_scores", "box_players", "rosters"):
        f = RAW / str(y) / f"{name}.csv"
        d[name] = pd.read_csv(f) if f.exists() else pd.DataFrame()
    return d


def team_owner_map(draft: pd.DataFrame, standings: pd.DataFrame) -> dict:
    m = {}
    for df in (draft, standings):
        if len(df) and {"team_id", "owner"} <= set(df.columns):
            for _, r in df[["team_id", "owner"]].dropna().drop_duplicates().iterrows():
                m[int(r["team_id"])] = str(r["owner"])
    return m


def build() -> pd.DataFrame:
    seasons = pd.read_csv(PROC / "player_seasons.csv")[["player_id", "year", "pos", "points"]]
    spec = slot_spec(CFG["historical"])
    rows = []
    for y in years():
        data = load_year(y)
        draft, standings = data["draft"], data["standings"]
        weekly, box = data["weekly_scores"], data["box_players"]
        if not len(draft) or not len(standings):
            continue
        t2o = team_owner_map(draft, standings)
        standings = standings.copy()
        standings["final_standing"] = standings["final_standing"].fillna(standings["reg_season_standing"])

        # ---- draft attributes ----
        dr = draft.merge(seasons, on="player_id", how="left",
                         suffixes=("", "_act")).query("year == @y or year != year")
        dr = draft.merge(seasons[seasons["year"] == y], on="player_id", how="left")
        dr["pos_b"] = dr["pos"].fillna("").map(bucket)
        dr.loc[dr["pos_b"] == "", "pos_b"] = dr["position"].fillna("").map(bucket)
        dr["points"] = dr["points"].fillna(0.0)
        dr["vorp"] = (dr["points"] - dr["pos_b"].map(REPL).fillna(0.0)).clip(lower=0)

        # ---- weekly team scores (regular season) ----
        wk = weekly[weekly.get("is_regular", True) == True].copy() if len(weekly) else pd.DataFrame()

        # ---- box scores (regular season) ----
        bx = box[box.get("is_playoff", False) == False].copy() if len(box) else pd.DataFrame()
        drafted_by_team = dr.groupby("team_id")["player_id"].agg(set).to_dict()

        for _, s in standings.iterrows():
            tid, owner = int(s["team_id"]), str(s["owner"])
            g = dr[dr["team_id"] == tid]
            if not len(g):
                continue
            early = g[g["round_num"] <= 5]
            mid = g[(g["round_num"] >= 6) & (g["round_num"] <= 9)]
            late = g[g["round_num"] >= 10]
            tot_vorp = g["vorp"].sum()
            hhi = ((g["vorp"] / tot_vorp) ** 2).sum() if tot_vorp > 0 else np.nan
            qb = g[g["pos_b"] == "QB"]
            row = {
                "year": y, "owner": owner, "team_id": tid,
                "final_standing": s["final_standing"], "wins": s.get("wins"),
                "points_for": s.get("points_for"), "champion": int(s["final_standing"] == 1),
                # draft
                "draft_vorp": tot_vorp, "early_vorp": early["vorp"].sum(),
                "mid_vorp": mid["vorp"].sum(), "late_vorp": late["vorp"].sum(),
                "draft_hhi": hhi,
                "hit_rate_r1_9": (g[g["round_num"] <= 9]["vorp"] > 0).mean(),
                "qb_first_round": int(qb["round_num"].min()) if len(qb) else 99,
                "streamer_picks_r1_9": int(((g["round_num"] <= 9) & g["pos_b"].isin(["K", "DST", "IDP"])).sum()),
            }
            # in-season: start/sit + waiver + streaming + consistency
            if len(bx):
                tb = bx[bx["team_id"] == tid]
                eff, left, undrafted_started, started_total = [], [], 0.0, 0.0
                streamers = set()
                for wknum, gg in tb.groupby("week"):
                    roster = [{"name": r["player_name"], "pos": r["position"], "proj": r["points"]}
                              for _, r in gg.iterrows() if pd.notna(r["points"])]
                    if len(roster) < len(spec):
                        continue
                    actual = gg[gg["started"] == True]["points"].sum()
                    opt = optimal_lineup(roster, spec)["total"]
                    if opt > 0:
                        eff.append(actual / opt)
                        left.append(opt - actual)
                    st = gg[gg["started"] == True]
                    started_total += st["points"].sum()
                    dset = drafted_by_team.get(tid, set())
                    undrafted_started += st[~st["player_id"].isin(dset)]["points"].sum()
                    streamers |= set(st[st["position"].fillna("").map(bucket).isin(["K", "DST", "IDP"])]["player_id"])
                row["startsit_eff"] = np.mean(eff) if eff else np.nan
                row["bench_left_wk"] = np.mean(left) if left else np.nan
                row["waiver_started_pts"] = undrafted_started
                row["waiver_share"] = (undrafted_started / started_total) if started_total else np.nan
                row["streamers_used"] = len(streamers)
            # luck: all-play
            if len(wk):
                tw = wk[wk["team_id"] == tid]
                ap_wins, games, act_wins = 0.0, 0, 0
                for wknum, ggw in wk.groupby("week"):
                    mine = ggw[ggw["team_id"] == tid]
                    if not len(mine):
                        continue
                    my_pts = float(mine.iloc[0]["points"])
                    others = ggw[ggw["team_id"] != tid]["points"].astype(float)
                    if len(others):
                        ap_wins += (my_pts > others).mean()
                        games += 1
                    act_wins += int(mine.iloc[0].get("outcome") == "W")
                row["allplay_wins"] = ap_wins
                row["luck_wins"] = act_wins - ap_wins if games else np.nan
                row["weekly_std"] = tw["points"].astype(float).std()
                row["weekly_median"] = tw["points"].astype(float).median()
            rows.append(row)
    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> None:
    df = df.dropna(subset=["final_standing"]).copy()
    print(f"team-seasons: {len(df)} across {df['year'].nunique()} seasons\n")

    metrics = ["draft_vorp", "early_vorp", "mid_vorp", "late_vorp", "draft_hhi",
               "hit_rate_r1_9", "qb_first_round", "streamer_picks_r1_9",
               "startsit_eff", "bench_left_wk", "waiver_started_pts", "waiver_share",
               "streamers_used", "weekly_std", "weekly_median", "allplay_wins", "luck_wins"]
    print("=== 1) WHAT PREDICTS FINISH (Spearman vs final_standing; NEGATIVE = attribute helps) ===")
    res = []
    for m in metrics:
        d = df.dropna(subset=[m])
        if len(d) < 20:
            continue
        rs, ps = spearmanr(d[m], d["final_standing"])
        rp, _ = spearmanr(d[m], d["points_for"])
        res.append((m, rs, ps, rp, len(d)))
    for m, rs, ps, rp, n in sorted(res, key=lambda x: x[1]):
        sig = "**" if ps < 0.01 else ("*" if ps < 0.05 else "  ")
        print(f"  {m:<22} finish {rs:+.2f}{sig}  pts-for {rp:+.2f}   (n={n})")

    print("\n=== 2) REPEATABILITY — is it a SKILL? (owner year-over-year autocorr, lag-1) ===")
    rep = []
    for m in metrics + ["points_for", "final_standing"]:
        pairs = []
        for o, g in df.sort_values("year").groupby("owner"):
            v = g[m].dropna().values
            pairs += [(v[i], v[i + 1]) for i in range(len(v) - 1)]
        if len(pairs) >= 15:
            a, b = zip(*pairs)
            r, p = spearmanr(a, b)
            rep.append((m, r, p, len(pairs)))
    for m, r, p, n in sorted(rep, key=lambda x: -x[1]):
        tag = "SKILL" if (r > 0.25 and p < 0.1) else ("maybe" if r > 0.15 else "luck-ish")
        print(f"  {m:<22} r={r:+.2f} (p={p:.2f}, n={n})  -> {tag}")

    print("\n=== 3) CHAMPION PROFILE (7 champs' league-percentile per attribute, median) ===")
    champ_pct = {}
    for m in metrics:
        pcts = []
        for y, g in df.groupby("year"):
            g = g.dropna(subset=[m])
            c = g[g["champion"] == 1]
            if len(c) and len(g) > 3:
                pcts.append((g[m] <= c.iloc[0][m]).mean() if m != "qb_first_round"
                            else (g[m] >= c.iloc[0][m]).mean())
        if pcts:
            champ_pct[m] = np.median(pcts)
    for m, v in sorted(champ_pct.items(), key=lambda x: -x[1]):
        print(f"  {m:<22} champ at {v:.0%} of league")

    print("\n=== 4) JRAY (jbray5) per-year vs champion ===")
    jr = df[df["owner"] == "jbray5"].sort_values("year")
    cols = ["year", "final_standing", "draft_vorp", "startsit_eff", "waiver_started_pts",
            "streamers_used", "luck_wins"]
    print(jr[cols].to_string(index=False,
          formatters={"startsit_eff": "{:.2f}".format, "draft_vorp": "{:.0f}".format,
                      "waiver_started_pts": "{:.0f}".format, "luck_wins": "{:+.1f}".format}))


if __name__ == "__main__":
    df = build()
    out = PROC / "league_dna.csv"
    df.to_csv(out, index=False)
    print(f"-> {out.relative_to(ROOT)}\n")
    report(df)
