// utils/players.js
import { normText } from './text.js';

export const toPosClass = (posRaw) => {
  const raw = (posRaw ?? '').toLowerCase().replace(/[\/\s.-]/g, '');
  if (raw.startsWith('rb')) return 'pos-rb';
  if (raw.startsWith('wr')) return 'pos-wr';
  if (raw === 'qb') return 'pos-qb';
  if (raw === 'te') return 'pos-te';
  if (raw === 'k' || raw === 'pk') return 'pos-k';
  if (['dst','def','defense','d'].includes(raw)) return 'pos-dst';
  if (['idp','lb','ilb','olb','edge','de','dt','dl','cb','s','ss','fs','db'].includes(raw)) return 'pos-idp';
  return '';
};

export function normalizePlayer(raw, indexIfNeeded) {
  const playerId = raw?.playerId ?? raw?.PlayerID ?? raw?.player_id ?? String(Math.random());
  const name = raw?.name ?? raw?.Name_stats ?? raw?.Name ?? 'Unknown';
  const team = raw?.team ?? raw?.Team_stats ?? raw?.Team ?? '';
  const position = raw?.position ?? raw?.Position_stats ?? raw?.Position ?? '';
  const pointsRaw = raw?.points ?? raw?.FantasyPointsPPR ?? raw?.FantasyPoints ?? null;
  const points = typeof pointsRaw === 'number' ? pointsRaw : pointsRaw != null ? Number(pointsRaw) : null;
  const rank = typeof raw?.rank === 'number' ? raw.rank : (typeof indexIfNeeded === 'number' ? indexIfNeeded + 1 : null);
  const adp = (typeof raw?.adp === 'number' ? raw.adp : null) ??
              raw?.AverageDraftPositionPPR ??
              raw?.AverageDraftPosition ?? null;
  return { playerId: String(playerId), name, team, position, points, rank, adp };
}

export function calculateDraftMetrics(players) {
  if (!Array.isArray(players)) return [];
  const replacementRanks = { QB: 12, RB: 24, WR: 30, TE: 12, K: 12, DST: 12, IDP: 12 };

  const byPos = players.reduce((acc, p) => {
    const pos = (p?.position || '').toUpperCase();
    if (!acc[pos]) acc[pos] = [];
    acc[pos].push(p);
    return acc;
  }, {});
  Object.values(byPos).forEach(list => list.sort((a, b) => (b?.points ?? 0) - (a?.points ?? 0)));

  const replacementPoints = {};
  for (const pos in byPos) {
    const idx = (replacementRanks[pos] ?? 12) - 1;
    replacementPoints[pos] = byPos[pos][idx]?.points ?? 0;
  }

  const withMetrics = players.map(p => {
    const posList = byPos[(p.position || '').toUpperCase()] || [];
    const idx = posList.findIndex(x => x?.playerId === p.playerId);
    const nextPoints = posList[idx + 1]?.points ?? p.points ?? 0;
    const cliff = (p.points ?? 0) - nextPoints;
    const vorp = (p.points ?? 0) - (replacementPoints[(p.position || '').toUpperCase()] ?? 0);
    return { ...p, vorp, cliff };
  });

  const getRange = key => {
    const vals = withMetrics.map(p => p?.[key]).filter(v => typeof v === 'number' && !isNaN(v));
    if (!vals.length) return { min: 0, max: 0 };
    return { min: Math.min(...vals), max: Math.max(...vals) };
  };

  const ranges = {
    adp: getRange('adp'),
    points: getRange('points'),
    vorp: getRange('vorp'),
    cliff: getRange('cliff'),
  };
  const norm = (val, { min, max }, invert = false) =>
    (val == null || isNaN(val) || max === min) ? 0 :
    invert ? 1 - (val - min) / (max - min) : (val - min) / (max - min);

  return withMetrics.map(p => {
    const adpNorm = norm(p.adp, ranges.adp, true);
    const pointsNorm = norm(p.points, ranges.points);
    const vorpNorm = norm(p.vorp, ranges.vorp);
    const cliffNorm = norm(p.cliff, ranges.cliff);
    const compositeScore = adpNorm * 0.45 + pointsNorm * 0.15 + vorpNorm * 0.20 + cliffNorm * 0.10;
    return { ...p, compositeScore };
  });
}

export function matchesPlayer(p, q) {
  const qn = normText(q);
  if (!qn) return true;
  const hay = normText(`${p?.name ?? ''} ${p?.team ?? ''} ${p?.position ?? ''}`);
  return qn.split(' ').every(t => hay.includes(t));
}
