"""Verify the grounded ceiling/upside findings against the league's own 7-yr record."""
import csv, json, math
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent
YEARS = range(2019, 2026)
repl = json.loads((ROOT/"data"/"processed"/"replacement_levels.json").read_text())["historical"]

# player_seasons: (player_id, year) -> points
ps = {}
for r in csv.DictReader(open(ROOT/"data"/"processed"/"player_seasons.csv", encoding="utf-8")):
    try:
        ps[(int(r["player_id"]), int(r["year"]))] = (float(r["points"]), r["pos"])
    except (ValueError, KeyError):
        pass

def norm_pos(p):
    p = (p or "").upper()
    if p in ("DST","D/ST","DEF"): return "DST"
    if p in ("DP","IDP","LB","DB","DL"): return "IDP"
    return p

# Build draft picks joined to actual points, plus standings
picks = []  # dict per pick
standings = {}  # (owner, year) -> (final, points_for)
for y in YEARS:
    for r in csv.DictReader(open(ROOT/f"data/raw/espn/{y}/standings.csv", encoding="utf-8")):
        standings[(r["owner"], y)] = (int(r["final_standing"]), float(r["points_for"]))
    for r in csv.DictReader(open(ROOT/f"data/raw/espn/{y}/draft.csv", encoding="utf-8")):
        try:
            pid = int(r["player_id"]); ov = int(r["overall_pick"]); rd = int(r["round_num"])
        except ValueError:
            continue
        pt = ps.get((pid, y))
        if pt is None:
            continue
        pos = norm_pos(r["position"])
        actual = pt[0]
        vorp = actual - repl.get(pos, 0.0)
        picks.append({"owner": r["owner"], "year": y, "ov": ov, "rd": rd,
                      "pos": pos, "name": r["player_name"], "actual": actual, "vorp": vorp})

print(f"picks joined: {len(picks)}")

# Empirical draft-cost curve (smoothed mean actual by overall_pick) for raw surplus
from statistics import mean
by_ov = defaultdict(list)
for p in picks:
    by_ov[p["ov"]].append(p["actual"])
ovs = sorted(by_ov)
curve = {}
for ov in ovs:
    win = [v for o in range(ov-5, ov+6) for v in by_ov.get(o, [])]
    curve[ov] = mean(win) if win else mean(by_ov[ov])
for p in picks:
    p["surplus"] = p["actual"] - curve.get(p["ov"], 0.0)

# Aggregate to team-seasons
team = defaultdict(list)
for p in picks:
    team[(p["owner"], p["year"])].append(p)

rows = []
for (owner, y), plist in team.items():
    st = standings.get((owner, y))
    if not st:
        continue
    final, pf = st
    vorps = sorted((p["vorp"] for p in plist), reverse=True)
    surpluses = sorted((p["surplus"] for p in plist), reverse=True)
    total_vorp = sum(p["vorp"] for p in plist)
    top3_vorp = sum(vorps[:3])
    max_vorp = vorps[0] if vorps else 0
    max_surplus = surpluses[0] if surpluses else 0
    rows.append({"owner": owner, "year": y, "final": final, "pf": pf,
                 "total_vorp": total_vorp, "top3_vorp": top3_vorp,
                 "max_vorp": max_vorp, "max_surplus": max_surplus})

print(f"team-seasons: {len(rows)}")

def pearson(a, b):
    n = len(a); ma = mean(a); mb = mean(b)
    num = sum((x-ma)*(y-mb) for x,y in zip(a,b))
    da = math.sqrt(sum((x-ma)**2 for x in a)); db = math.sqrt(sum((y-mb)**2 for y in b))
    return num/(da*db) if da and db else 0.0

pf = [r["pf"] for r in rows]
fin = [r["final"] for r in rows]
tv = [r["total_vorp"] for r in rows]
t3 = [r["top3_vorp"] for r in rows]
mv = [r["max_vorp"] for r in rows]
msr = [r["max_surplus"] for r in rows]

print("\n=== CEILING vs BREADTH (points_for / final_standing) ===")
print(f"total_vorp : PF {pearson(tv,pf):+.3f}  finish {pearson(tv,fin):+.3f}")
print(f"top3_vorp  : PF {pearson(t3,pf):+.3f}  finish {pearson(t3,fin):+.3f}")
print(f"max_vorp   : PF {pearson(mv,pf):+.3f}  finish {pearson(mv,fin):+.3f}")
print(f"max_surplus(raw, QB-artifact): PF {pearson(msr,pf):+.3f}  finish {pearson(msr,fin):+.3f}")

# Top-25 biggest raw surplus picks: are they QBs?
allp = sorted(picks, key=lambda p:-p["surplus"])[:25]
qbn = sum(1 for p in allp if p["pos"]=="QB")
print(f"\nTop-25 raw-surplus picks that are QB: {qbn}/25")
print("top 6:", [(p["name"], p["pos"], p["year"], round(p["surplus"])) for p in allp[:6]])

# Top-40 biggest VORP hits by position
allv = sorted(picks, key=lambda p:-p["vorp"])[:40]
posc = defaultdict(int)
for p in allv: posc[p["pos"]]+=1
print(f"\nTop-40 VORP hits by pos: {dict(posc)}")

# JRay specifically
print("\n=== JRay (jbray5) by year ===")
jr = sorted((r for r in rows if r["owner"]=="jbray5"), key=lambda r:r["year"])
for r in jr:
    print(f"{r['year']} finish={r['final']:>2} pf={r['pf']:.0f} total_vorp={r['total_vorp']:.0f} "
          f"top3_vorp={r['top3_vorp']:.0f} max_vorp={r['max_vorp']:.0f}")
# rank JRay's total_vorp and top3_vorp within each year
print("\nJRay rank within year (1=best of 12):")
for y in YEARS:
    yr = [r for r in rows if r["year"]==y]
    if not any(r["owner"]=="jbray5" for r in yr): continue
    tv_sorted = sorted(yr, key=lambda r:-r["total_vorp"])
    t3_sorted = sorted(yr, key=lambda r:-r["top3_vorp"])
    jrr = next(r for r in yr if r["owner"]=="jbray5")
    tvr = tv_sorted.index(jrr)+1; t3r = t3_sorted.index(jrr)+1
    print(f"{y} finish={jrr['final']:>2}  total_vorp rank {tvr}/{len(yr)}  top3_vorp rank {t3r}/{len(yr)}")

# Champions: top3 vs total edge over field
print("\n=== Champions vs field ===")
champs = [r for r in rows if r["final"]==1]
field = [r for r in rows if r["final"]!=1]
print(f"champs n={len(champs)}")
print(f"top3_vorp: champ {mean(r['top3_vorp'] for r in champs):.0f} vs field {mean(r['top3_vorp'] for r in field):.0f}  (gap {mean(r['top3_vorp'] for r in champs)-mean(r['top3_vorp'] for r in field):+.0f})")
print(f"total_vorp: champ {mean(r['total_vorp'] for r in champs):.0f} vs field {mean(r['total_vorp'] for r in field):.0f}  (gap {mean(r['total_vorp'] for r in champs)-mean(r['total_vorp'] for r in field):+.0f})")
rest = lambda r: r["total_vorp"]-r["top3_vorp"]
print(f"rest(total-top3): champ {mean(rest(r) for r in champs):.0f} vs field {mean(rest(r) for r in field):.0f}  (gap {mean(rest(r) for r in champs)-mean(rest(r) for r in field):+.0f})")
