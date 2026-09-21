"""Which board does each manager draft (and therefore trade) off — market/ESPN
defaults, or FantasyPros expert ranks?

The fingerprint: on 2026 draft picks where the market price (fp_adp — the
cross-site ADP the ESPN room's default ordering tracks) and the FP expert rank
(overall ECR) DISAGREED by a round or more, which side did the manager take?
A pick that lands near ECR while defying ADP is an expert-list follower; the
reverse is a market/default follower. n is small (a handful of divergent picks
per manager) so every verdict carries its sample size — treat 'lean', not law.

Output: data/processed/manager_lens.json {uiTeam: {lens, nDivergent, ecrFollows,
adpFollows, confidence}}. Run `python models/manager_lens.py` to rebuild.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed" / "manager_lens.json"
DIVERGENCE = 12          # ADP vs ECR must disagree by at least ~a round


def _board():
    rows = {}
    with open(ROOT / "data" / "processed" / "board_2026.csv", newline="",
              encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                fp_adp = float(r.get("fp_adp") or "nan")
                ecr = float(r.get("ecr") or "nan")
            except ValueError:
                continue
            if fp_adp == fp_adp and ecr == ecr:      # not NaN
                rows[r["name"]] = (fp_adp, ecr)
    return rows


def build(season: int = 2026) -> dict:
    try:
        from espn_sync import get_draft
    except ImportError:
        from models.espn_sync import get_draft
    board = _board()
    board_rows = [{"name": n, "pos": "?"} for n in board]   # names for the mapper
    draft = get_draft(board_rows, year=season)
    tallies: dict[str, dict] = {}
    for pk in draft.get("picks", []):
        ui, name, overall = pk.get("uiTeam"), pk.get("boardName"), pk.get("overall")
        if not ui or not name or name not in board:
            continue
        fp_adp, ecr = board[name]
        if abs(fp_adp - ecr) < DIVERGENCE:
            continue                                  # sources agree — no signal
        t = tallies.setdefault(ui, {"ecrFollows": 0, "adpFollows": 0, "picks": []})
        follows_ecr = abs(overall - ecr) < abs(overall - fp_adp)
        t["ecrFollows" if follows_ecr else "adpFollows"] += 1
        t["picks"].append({"name": name, "overall": overall,
                           "adp": fp_adp, "ecr": ecr,
                           "took": "ECR" if follows_ecr else "ADP"})
    out = {}
    for ui, t in tallies.items():
        n = t["ecrFollows"] + t["adpFollows"]
        margin = abs(t["ecrFollows"] - t["adpFollows"])
        lens = ("FP-expert" if t["ecrFollows"] > t["adpFollows"]
                else "ESPN/market" if t["adpFollows"] > t["ecrFollows"] else "split")
        conf = "medium" if (n >= 5 and margin >= 2) else "low"
        out[ui] = {"lens": lens, "nDivergent": n, "ecrFollows": t["ecrFollows"],
                   "adpFollows": t["adpFollows"], "confidence": conf,
                   "evidence": t["picks"][:6]}
    OUT.write_text(json.dumps({"season": season, "divergenceBar": DIVERGENCE,
                               "teams": out}, indent=1), encoding="utf-8")
    return out


def get() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8")).get("teams", {})
    except OSError:
        return {}


if __name__ == "__main__":
    res = build()
    print(f"{'TEAM':<10} {'LENS':<12} {'ECR':>4} {'ADP':>4}  conf")
    for ui, t in sorted(res.items()):
        print(f"{ui:<10} {t['lens']:<12} {t['ecrFollows']:>4} {t['adpFollows']:>4}  {t['confidence']}")
