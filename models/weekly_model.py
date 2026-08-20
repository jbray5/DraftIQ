"""Weekly player-points ML model — the start/sit brain, with an honest gate.

Task: predict a player's ACTUAL weekly fantasy points (league scoring) from
information available before kickoff. The benchmark to beat is ESPN's own
weekly projection (`projected_points` in our historical box scores) — if the
model can't beat the vendor out-of-sample, it does not get wired into the
portal. (Same discipline as the draft-side dart backtest.)

Data: data/raw/espn/{year}/box_players.csv, 2019-2025 (~24k player-weeks of
this league's rostered players, league-scored, with ESPN's projection stored
alongside the actual).

Features (all leak-free, known pre-kickoff):
  * espn_proj — the vendor projection itself (the model learns corrections)
  * trailing same-season actuals: mean/max of prior weeks, games played
  * trailing projection BIAS: mean(actual − proj) over prior weeks — "does
    ESPN systematically over/under-rate this player right now?"
  * prior-season points-per-week
  * position, week number

Split: train 2019-2023, test 2024-2025 (two full held-out seasons).
Metrics: MAE, Pearson r, and DECISION accuracy — among same-position pairs
whose projections are within 3 pts (the coin-flip start/sit calls the portal
actually faces), how often does each ranker pick the player who scored more?

Run: python models/weekly_model.py   (trains, evaluates, saves the model +
verdict to data/processed/weekly_model.json for the portal to consult)
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed"
POS = ["QB", "RB", "WR", "TE", "K", "DST", "IDP"]


def bucket(p: str) -> str:
    p = str(p).upper().replace("/", "").replace(".", "")
    if p in ("QB", "RB", "WR", "TE", "K"):
        return p
    if p in ("DST", "DEF", "D"):
        return "DST"
    return "IDP"


def load_frames() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(ROOT / "data" / "raw" / "espn" / "*" / "box_players.csv"))):
        year = int(os.path.basename(os.path.dirname(f)))
        d = pd.read_csv(f)
        d["year"] = year
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df = df[(df["is_playoff"] == False) & (df["on_bye"] == False)]          # noqa: E712
    df = df[pd.to_numeric(df["points"], errors="coerce").notna()
            & pd.to_numeric(df["projected_points"], errors="coerce").notna()]
    df["pos"] = df["position"].map(bucket)
    df = df[df["projected_points"] > 0]           # ESPN declined to project -> skip
    return df.sort_values(["player_id", "year", "week"]).reset_index(drop=True)


def engineer(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["player_id", "year"], group_keys=False)
    # trailing same-season signals, shifted so week N sees only weeks < N
    df["trail_mean"] = g["points"].apply(lambda s: s.shift(1).expanding().mean())
    df["trail_max"] = g["points"].apply(lambda s: s.shift(1).expanding().max())
    df["games_so_far"] = g.cumcount()
    df["bias"] = df["points"] - df["projected_points"]
    df["trail_bias"] = g["bias"].apply(lambda s: s.shift(1).expanding().mean())
    # prior-season points-per-week
    season_ppw = (df.groupby(["player_id", "year"])["points"].mean()
                  .rename("ppw").reset_index())
    season_ppw["year"] += 1
    df = df.merge(season_ppw.rename(columns={"ppw": "prev_ppw"}),
                  on=["player_id", "year"], how="left")
    for c in ("trail_mean", "trail_max", "trail_bias", "prev_ppw"):
        df[c] = df[c].fillna(df["projected_points"] if c in ("trail_mean", "trail_max")
                             else 0.0)
    for p in POS:
        df[f"pos_{p}"] = (df["pos"] == p).astype(int)
    return df


FEATURES = (["projected_points", "trail_mean", "trail_max", "trail_bias",
             "games_so_far", "prev_ppw", "week"] + [f"pos_{p}" for p in POS])


def decision_accuracy(d: pd.DataFrame, score_col: str, n_pairs: int = 20000,
                      seed: int = 7) -> float:
    """Among same-position, same-week pairs with |proj diff| <= 3 (real start/sit
    coin flips), how often does `score_col` pick the player who actually scored more?"""
    rng = np.random.default_rng(seed)
    correct = total = 0
    for (_, _, _), grp in d.groupby(["year", "week", "pos"]):
        if len(grp) < 2:
            continue
        arr = grp[[score_col, "projected_points", "points"]].to_numpy()
        k = min(len(arr) * 2, 40)
        for _ in range(k):
            i, j = rng.choice(len(arr), 2, replace=False)
            if abs(arr[i][1] - arr[j][1]) > 3 or arr[i][2] == arr[j][2]:
                continue
            pick = i if arr[i][0] > arr[j][0] else j
            actual = i if arr[i][2] > arr[j][2] else j
            correct += (pick == actual)
            total += 1
            if total >= n_pairs:
                return correct / total
    return correct / total if total else float("nan")


def main() -> None:
    from sklearn.ensemble import HistGradientBoostingRegressor
    df = engineer(load_frames())
    train = df[df["year"] <= 2023]
    test = df[df["year"] >= 2024].copy()
    print(f"train: {len(train)} player-weeks (2019-2023) · test: {len(test)} (2024-2025)")

    model = HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.05, max_depth=None,
        min_samples_leaf=40, l2_regularization=1.0, random_state=7)
    model.fit(train[FEATURES], train["points"])
    test["ml"] = model.predict(test[FEATURES])

    mae_espn = float((test["points"] - test["projected_points"]).abs().mean())
    mae_ml = float((test["points"] - test["ml"]).abs().mean())
    r_espn = float(np.corrcoef(test["projected_points"], test["points"])[0, 1])
    r_ml = float(np.corrcoef(test["ml"], test["points"])[0, 1])
    acc_espn = decision_accuracy(test, "projected_points")
    acc_ml = decision_accuracy(test, "ml")

    print(f"\n{'metric':<28}{'ESPN proj':>12}{'ML model':>12}")
    print(f"{'MAE (pts, lower=better)':<28}{mae_espn:>12.2f}{mae_ml:>12.2f}")
    print(f"{'Pearson r (higher=better)':<28}{r_espn:>12.3f}{r_ml:>12.3f}")
    print(f"{'coin-flip decision acc':<28}{acc_espn:>12.1%}{acc_ml:>12.1%}")

    wins = (mae_ml < mae_espn) + (r_ml > r_espn) + (acc_ml > acc_espn)
    verdict = "SHIP" if wins >= 2 else "DO NOT SHIP"
    print(f"\nVERDICT: {verdict} — model wins {wins}/3 metrics vs the vendor.")
    if verdict == "SHIP":
        try:
            import joblib
            joblib.dump({"model": model, "features": FEATURES},
                        OUT / "weekly_model.joblib")
            print(f"saved -> {OUT / 'weekly_model.joblib'}")
        except Exception as e:
            print(f"(model save skipped: {e})")
    (OUT / "weekly_model.json").write_text(json.dumps({
        "verdict": verdict, "trainedThrough": 2023, "testedOn": [2024, 2025],
        "mae": {"espn": round(mae_espn, 3), "ml": round(mae_ml, 3)},
        "pearson": {"espn": round(r_espn, 4), "ml": round(r_ml, 4)},
        "decisionAcc": {"espn": round(acc_espn, 4), "ml": round(acc_ml, 4)},
        "features": FEATURES}, indent=1), encoding="utf-8")
    print(f"eval card -> {OUT / 'weekly_model.json'}")


if __name__ == "__main__":
    main()
