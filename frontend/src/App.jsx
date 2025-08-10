import React, { useEffect, useReducer, useCallback, useRef, useState } from 'react';
import './App.css';

const TEAM_NAMES = [
  'Wester🏆', 'Spivey🏆🏆🏆', 'D-Put', 'Walker', 'Will', 'Thom🏆🏆',
  'JRay🏆🏆🏆', 'Taylor', 'Mac', 'Cuda', 'CJ🏆', 'Josh'
];
const ROSTER_SLOTS = ['QB', 'RB1', 'RB2', 'WR1', 'WR2', 'TE', 'FLEX', 'IDP', 'D/ST', 'K', 'Bench 1', 'Bench 2', 'Bench 3', 'Bench 4', 'Bench 5', 'Bench 6', 'Bench 7'];
const STORAGE_KEY = 'ffd_draft_state_v4';
const HEIGHT_KEY = 'ffd_board_height';

// --- Helpers ---
function buildEmptyTeams(names, slots) {
  const t = {};
  names.forEach(n => {
    t[n] = {};
    slots.forEach(s => (t[n][s] = null));
  });
  return t;
}

function normalizeName(s = '') {
  return s.toLowerCase().replace(/\s+/g, '');
}

function migrateTeams(savedTeams, names, slots) {
  const empty = buildEmptyTeams(names, slots);
  if (!savedTeams || typeof savedTeams !== 'object') return empty;

  const nameIndex = Object.fromEntries(names.map(n => [normalizeName(n), n]));
  Object.entries(savedTeams).forEach(([oldName, slotsObj]) => {
    const target = nameIndex[normalizeName(oldName)];
    if (target) {
      empty[target] = { ...empty[target], ...slotsObj };
    }
  });

  return empty;
}

function toPosClass(posRaw) {
  const raw = (posRaw ?? '').toString().toLowerCase().replace(/[\/\s.-]/g, '');
  if (raw.startsWith('rb')) return 'pos-rb';
  if (raw.startsWith('wr')) return 'pos-wr';
  if (raw === 'qb') return 'pos-qb';
  if (raw === 'te') return 'pos-te';
  if (raw === 'k' || raw === 'pk') return 'pos-k';
  if (['dst','def','defense','d'].includes(raw)) return 'pos-dst';
  if (['idp','lb','ilb','olb','edge','de','dt','dl','cb','s','ss','fs','db'].includes(raw)) return 'pos-idp';
  return '';
}

function normalizePlayer(raw, indexIfNeeded) {
  const playerId =
    raw?.playerId ?? raw?.PlayerID ?? raw?.player_id ?? String(Math.random());
  const name =
    raw?.name ?? raw?.Name_stats ?? raw?.Name ?? 'Unknown';
  const team =
    raw?.team ?? raw?.Team_stats ?? raw?.Team ?? '';
  const position =
    raw?.position ?? raw?.Position_stats ?? raw?.Position ?? '';

  const pointsRaw = raw?.points ?? raw?.FantasyPointsPPR ?? raw?.FantasyPoints ?? null;
  const points =
    typeof pointsRaw === 'number'
      ? pointsRaw
      : pointsRaw != null
      ? Number(pointsRaw)
      : null;

  const rank =
    typeof raw?.rank === 'number' ? raw.rank :
    (typeof indexIfNeeded === 'number' ? indexIfNeeded + 1 : null);

  const adp =
    (typeof raw?.adp === 'number' ? raw.adp : null) ??
    raw?.AverageDraftPositionPPR ??
    raw?.AverageDraftPosition ?? null;

  return { playerId: String(playerId), name, team, position, points, rank, adp };
}

function calculateDraftMetrics(players) {
  if (!Array.isArray(players)) return [];

  const replacementRanks = { QB: 12, RB: 24, WR: 30, TE: 12, K: 12, DST: 12, IDP: 12 };

  const byPos = players.reduce((acc, p) => {
    const pos = (p?.position || '').toUpperCase();
    if (!acc[pos]) acc[pos] = [];
    acc[pos].push(p);
    return acc;
  }, {});

  Object.values(byPos).forEach(list => {
    list.sort((a, b) => (b?.points ?? 0) - (a?.points ?? 0));
  });

  const replacementPoints = {};
  for (const pos in byPos) {
    const idx = (replacementRanks[pos] ?? 12) - 1;
    replacementPoints[pos] = byPos[pos][idx]?.points ?? 0;
  }

  const withMetrics = players.map(p => {
    if (!p) return {};
    const pos = (p.position || '').toUpperCase();
    const posList = byPos[pos] || [];
    const idx = posList.findIndex(x => x?.playerId === p.playerId);
    const nextPoints = posList[idx + 1]?.points ?? p.points ?? 0;
    const cliff = (p.points ?? 0) - nextPoints;
    const vorp = (p.points ?? 0) - (replacementPoints[pos] ?? 0);
    return { ...p, vorp, cliff };
  });

  const getRange = key => {
    const vals = withMetrics
      .map(p => p?.[key])
      .filter(v => typeof v === 'number' && !isNaN(v));
    if (vals.length === 0) return { min: 0, max: 0 };
    return { min: Math.min(...vals), max: Math.max(...vals) };
  };

  const ranges = {
    adp: getRange('adp'),
    points: getRange('points'),
    vorp: getRange('vorp'),
    cliff: getRange('cliff'),
  };

  const normalize = (val, { min, max }, invert = false) => {
    if (val == null || isNaN(val)) return 0;
    if (max === min) return 0;
    const norm = (val - min) / (max - min);
    return invert ? 1 - norm : norm;
  };

  return withMetrics.map(p => {
    if (!p) return {};
    const adpNorm = normalize(p.adp, ranges.adp, true);
    const pointsNorm = normalize(p.points, ranges.points);
    const vorpNorm = normalize(p.vorp, ranges.vorp);
    const cliffNorm = normalize(p.cliff, ranges.cliff);

    const compositeScore =
      adpNorm * 0.35 +
      pointsNorm * 0.30 +
      vorpNorm * 0.25 +
      cliffNorm * 0.10;

    return { ...p, compositeScore };
  });
}

function historyReducer(state, action) {
  const { past, present, future } = state;
  const push = (next) => ({ past: [...past, present], present: next, future: [] });

  switch (action.type) {
    case 'INIT': return { past: [], present: action.payload, future: [] };
    case 'APPLY': return push(action.payload);
    case 'UNDO':
      if (!past.length) return state;
      return { past: past.slice(0, -1), present: past[past.length - 1], future: [present, ...future] };
    case 'REDO':
      if (!future.length) return state;
      return { past: [...past, present], present: future[0], future: future.slice(1) };
    default: return state;
  }
}

export default function App() {
  const [state, dispatch] = useReducer(historyReducer, {
    past: [], present: null, future: []
  });

  // --- Board height state with persistence ---
  const [boardHeight, setBoardHeight] = useState(() => {
    const saved = localStorage.getItem(HEIGHT_KEY);
    return saved ? parseInt(saved, 10) : 300;
  });
  const isDraggingRef = useRef(false);

  const startDrag = () => { isDraggingRef.current = true; };
  const stopDrag = () => { isDraggingRef.current = false; };
  const onDrag = (e) => {
    if (!isDraggingRef.current) return;
    setBoardHeight(prev => {
      const newH = Math.max(150, prev + e.movementY);
      localStorage.setItem(HEIGHT_KEY, newH);
      return newH;
    });
  };

  useEffect(() => {
    window.addEventListener('mousemove', onDrag);
    window.addEventListener('mouseup', stopDrag);
    return () => {
      window.removeEventListener('mousemove', onDrag);
      window.removeEventListener('mouseup', stopDrag);
    };
  }, []);

  const draft = state?.present;
  const players = draft?.players ?? [];
  const teams = draft?.teams ?? {};

  const apply = useCallback((updater) => {
    if (!draft) return;
    dispatch({ type: 'APPLY', payload: updater(draft) });
  }, [draft]);

  useEffect(() => {
    if (draft) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
    }
  }, [draft]);

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        let withMetrics = calculateDraftMetrics(parsed.players || []);
        const migratedTeams = migrateTeams(parsed.teams, TEAM_NAMES, ROSTER_SLOTS);
        const sortedByADP = [...withMetrics].sort((a, b) => (a?.adp ?? 9999) - (b?.adp ?? 9999));
        dispatch({ type: 'INIT', payload: { players: sortedByADP, teams: migratedTeams } });
        return;
      } catch {
        localStorage.removeItem(STORAGE_KEY);
      }
    }

    (async () => {
      try {
        const res = await fetch('http://127.0.0.1:5001/api/get-ranked-players');
        const data = await res.json();
        let normalized = Array.isArray(data) ? data.map((row, i) => normalizePlayer(row, i)) : [];
        let withMetrics = calculateDraftMetrics(normalized);
        const sortedByADP = [...withMetrics].sort((a, b) => (a?.adp ?? 9999) - (b?.adp ?? 9999));
        dispatch({ type: 'INIT', payload: { players: sortedByADP, teams: buildEmptyTeams(TEAM_NAMES, ROSTER_SLOTS) } });
      } catch {
        dispatch({ type: 'INIT', payload: { players: [], teams: buildEmptyTeams(TEAM_NAMES, ROSTER_SLOTS) } });
      }
    })();
  }, []);

  const handleRefresh = async () => {
    try {
      const res = await fetch('http://127.0.0.1:5001/api/get-ranked-players');
      const data = await res.json();
      let normalized = Array.isArray(data) ? data.map((row, i) => normalizePlayer(row, i)) : [];
      let withMetrics = calculateDraftMetrics(normalized);
      const sortedByADP = [...withMetrics].sort((a, b) => (a?.adp ?? 9999) - (b?.adp ?? 9999));
      apply(cur => ({ ...cur, players: sortedByADP }));
    } catch {}
  };

  const allowDrop = (e) => e.preventDefault();
  const handleDragStart = (e, playerId) => e.dataTransfer.setData('playerId', String(playerId));

  const handleDrop = (e, teamName, slot) => {
    const playerId = e.dataTransfer.getData('playerId');
    apply(cur => {
      if (!cur || !cur.players || !cur.teams) return cur;
      const player = cur.players.find(p => p?.playerId === playerId);
      if (!player) return cur;

      const teamSlots = cur.teams[teamName] ?? {};
      const prevPlayer = teamSlots[slot];
      const nextPlayers = cur.players.filter(p => p?.playerId !== playerId);
      if (prevPlayer) {
        const insertAt = nextPlayers.findIndex(p => (p?.rank ?? Infinity) > (prevPlayer?.rank ?? Infinity));
        if (insertAt === -1) nextPlayers.push(prevPlayer);
        else nextPlayers.splice(insertAt, 0, prevPlayer);
      }

      return {
        ...cur,
        players: nextPlayers,
        teams: {
          ...cur.teams,
          [teamName]: { ...teamSlots, [slot]: { ...player, position: player.position } }
        }
      };
    });
  };

  const removeFromSlot = (teamName, slot) => {
    apply(cur => {
      if (!cur || !cur.teams) return cur;
      const teamSlots = cur.teams[teamName] ?? {};
      const player = teamSlots[slot];
      if (!player) return cur;

      const nextTeams = { ...cur.teams, [teamName]: { ...teamSlots, [slot]: null } };
      const nextPlayers = [...(cur.players ?? [])];
      const insertAt = nextPlayers.findIndex(p => (p?.rank ?? Infinity) > (player?.rank ?? Infinity));
      if (insertAt === -1) nextPlayers.push(player);
      else nextPlayers.splice(insertAt, 0, player);
      return { ...cur, teams: nextTeams, players: nextPlayers };
    });
  };

  const myTeamName = 'JRay';
  const myTeam = teams?.[myTeamName] ?? buildEmptyTeams([myTeamName], ROSTER_SLOTS)[myTeamName];

  if (!draft) return <main className="p-6 text-zinc-100">Loading draft...</main>;

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col">
      {/* HEADER */}
      <header className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur">
        <div className="container mx-auto max-w-7xl px-4 py-4 flex items-center justify-between gap-2">
          <h1 className="text-lg sm:text-xl font-semibold">Derek Jeter&apos;s Taco Hole Fantasy Draft - 2025</h1>
          <div className="flex gap-2">
            <button onClick={() => dispatch({ type: 'UNDO' })} disabled={!state.past.length} className="px-3 py-2 bg-zinc-800 rounded-xl disabled:opacity-50">Undo</button>
            <button onClick={() => dispatch({ type: 'REDO' })} disabled={!state.future.length} className="px-3 py-2 bg-zinc-800 rounded-xl disabled:opacity-50">Redo</button>
            <button onClick={handleRefresh} className="rounded-xl px-4 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-500">Refresh Rankings</button>
          </div>
        </div>
      </header>

      {/* TEAM BOARD */}
      <section
        aria-label="Teams"
        style={{ height: boardHeight }}
        className="overflow-x-auto overflow-y-auto"
      >
        <div className="flex flex-nowrap gap-4 min-w-max pb-2">
          {TEAM_NAMES.map((team) => {
            const teamSlots = teams?.[team] ?? {};
            return (
              <div key={team} className="w-64 shrink-0 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-3">
                <div className="mb-2 text-center text-sm font-semibold tracking-wide">{team}</div>
                <ul className="space-y-2">
                  {ROSTER_SLOTS.map((slot) => (
                    <li
                      key={slot}
                      onDrop={(e) => handleDrop(e, team, slot)}
                      onDragOver={allowDrop}
                      className={`slot-card ${toPosClass(teamSlots?.[slot]?.position)}
                        min-h-[44px] rounded-xl border border-zinc-800 bg-zinc-900
                        px-2 py-2 text-sm grid grid-cols-[56px_1fr_auto] items-center gap-2
                        hover:bg-zinc-800 transition`}
                    >
                      <span className="text-zinc-400 font-medium">{slot}</span>
                      <span className="truncate">{teamSlots?.[slot]?.name ?? '—'}</span>
                      {teamSlots?.[slot] && (
                        <button onClick={() => removeFromSlot(team, slot)} className="text-red-500 text-xs">✕</button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </section>

      {/* DRAG HANDLE */}
      <div
        onMouseDown={startDrag}
        className="h-2 cursor-row-resize bg-zinc-700 hover:bg-zinc-600"
      ></div>

      {/* MAIN GRID */}
      <div className="container mx-auto max-w-7xl px-4 py-6 space-y-6 flex-1 overflow-auto">
        <div className="grid grid-cols-12 gap-6">
          {/* YOUR TEAM */}
          <aside className="col-span-12 md:col-span-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
            <h2 className="mb-3 text-lg font-semibold text-center">Your Team</h2>
            <ul className="space-y-2">
              {ROSTER_SLOTS.map((slot) => (
                <li key={slot} className={`slot-card ${toPosClass(myTeam?.[slot]?.position)}
                  rounded-xl bg-zinc-900 px-3 py-2 text-sm border border-zinc-800`}>
                  <span className="text-zinc-400 font-medium">{slot}:</span>{' '}
                  <span className="truncate">{myTeam?.[slot]?.name ?? '—'}</span>
                </li>
              ))}
            </ul>
          </aside>

          {/* AVAILABLE PLAYERS */}
          <section className="col-span-12 md:col-span-6 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
            <h2 className="mb-4 text-lg font-semibold">Available Players</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {players.length === 0 ? (
                <p className="text-zinc-400">No players loaded.</p>
              ) : (
                players.map((player) => (
                  <article
                    key={player.playerId}
                    draggable
                    onDragStart={(e) => handleDragStart(e, player.playerId)}
                    className={`player-card ${toPosClass(player.position)} cursor-move rounded-xl border border-zinc-800 bg-zinc-900 p-3 hover:bg-zinc-800 transition`}
                  >
                    <h3 className="font-semibold leading-tight">
                      {player.rank ? `#${player.rank} ` : ''}{player.name}
                    </h3>
                    <p className="text-xs text-zinc-300 mb-2">
                      {player.position}{player.team ? ` • ${player.team}` : ''}
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-zinc-800 border border-zinc-700">
                        Pts (PPR) {player.points != null ? Number(player.points).toFixed(2) : '—'}
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-zinc-800 border border-zinc-700">
                        ADP {player.adp != null ? player.adp : '—'}
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-zinc-800 border border-zinc-700">
                        VORP {player.vorp != null ? player.vorp.toFixed(1) : '—'}
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-zinc-800 border border-zinc-700">
                        Cliff {player.cliff != null ? player.cliff.toFixed(1) : '—'}
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-800 border border-blue-600">
                        Score {player.compositeScore != null ? player.compositeScore.toFixed(3) : '—'}
                      </span>
                    </div>
                  </article>
                ))
              )}
            </div>
          </section>

          {/* DRAFT SUGGESTIONS */}
          <aside className="col-span-12 md:col-span-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
            <h2 className="mb-3 text-lg font-semibold text-center">Draft Suggestions</h2>
            {players.length > 0 ? (
              <div className="space-y-2">
                {[...players]
                  .sort((a, b) => (b?.compositeScore ?? 0) - (a?.compositeScore ?? 0))
                  .slice(0, 5)
                  .map((p, i) => (
                    <div
                      key={p.playerId}
                      className="rounded-xl border border-zinc-800 bg-zinc-900 p-3"
                    >
                      <p className="font-semibold">
                        #{i + 1} {p.name}{' '}
                        <span className="text-zinc-400 text-xs">
                          ({p.compositeScore?.toFixed(3) ?? '—'})
                        </span>
                      </p>
                      <p className="text-xs text-zinc-300">
                        {p.position}{p.team ? ` • ${p.team}` : ''}
                      </p>
                    </div>
                  ))}
              </div>
            ) : (
              <p className="text-zinc-400 text-sm">No suggestion available.</p>
            )}
          </aside>
        </div>
      </div>
    </main>
  );
}
