export function predictNextPick(players, draftOrder, pickIndex, rosters) {
  const team = draftOrder[pickIndex];
  const filled = getFilledRosterCounts(rosters[team]);
  const needs = { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, DST: 1, K: 1 };

  let best = null;
  let bestScore = -Infinity;

  for (let p of players) {
    if (Object.values(rosters).some(teamRoster =>
      Object.values(teamRoster).some(assigned => assigned?.PlayerID === p.PlayerID)
    )) continue;

    const pos = p.Position_stats;
    const flexEligible = ['RB', 'WR', 'TE'].includes(pos);
    const unfilled = needs[pos] && filled[pos] < needs[pos];
    const flexOpen = flexEligible && filled.FLEX < needs.FLEX;

    const weight = unfilled || flexOpen ? 1 : 0.5;
    const score = p.Composite_Score * weight;

    if (score > bestScore) {
      bestScore = score;
      best = p;
    }
  }

  return best
    ? `Next Best Pick for ${team}: ${best.Name_stats} (${best.Position_stats}) - ${best.Composite_Score.toFixed(2)}`
    : `No suitable picks for ${team}`;
}

function getFilledRosterCounts(roster) {
  const counts = { QB: 0, RB: 0, WR: 0, TE: 0, FLEX: 0, DST: 0, K: 0 };
  for (let [slot, player] of Object.entries(roster || {})) {
    if (!player) continue;
    const pos = player.Position_stats;
    if (slot === "FLEX") counts.FLEX++;
    else if (counts[pos] !== undefined) counts[pos]++;
  }
  return counts;
}
