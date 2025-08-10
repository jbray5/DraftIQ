import React, { useEffect, useReducer, useCallback } from 'react';

const TEAM_NAMES = [
  'Josh','Mac','D-Put','Jeff','Wester','J Ray',
  'Taylor','Will','Spivey','CJ','Walk','Thom'
];
const ROSTER_SLOTS = ['QB','RB1','RB2','WR1','WR2','TE','FLEX','IDP','K','Bench'];
const STORAGE_KEY = 'ffd_draft_state_v1';

// Normalize rows from either the old merged CSV or the new Baker rankings
function normalizePlayer(raw, indexIfNeeded) {
  const playerId =
    raw.playerId ?? raw.PlayerID ?? raw.player_id ?? String(Math.random());
  const name =
    raw.name ?? raw.Name_stats ?? raw.Name ?? 'Unknown';
  const team =
    raw.team ?? raw.Team_stats ?? raw.Team ?? '';
  const position =
    raw.position ?? raw.Position_stats ?? raw.Position ?? '';
  const points =
    (typeof raw.points === 'number' ? raw.points : null) ??
    (typeof raw.Composite_Score === 'number' ? raw.Composite_Score : null) ??
    raw.FantasyPointsHalfPPR ??
    raw.FantasyPointsPPR ??
    raw.FantasyPoints ?? null;
  const rank =
    typeof raw.rank === 'number' ? raw.rank :
    (typeof indexIfNeeded === 'number' ? indexIfNeeded + 1 : null);
  return { playerId: String(playerId), name, team, position, points, rank };
}

// --- History reducer for undo/redo ---
function historyReducer(state, action) {
  const { past, present, future } = state;
  const push = (next) => ({ past: [...past, present], present: next, future: [] });

  switch (action.type) {
    case 'INIT': return { past: [], present: action.payload, future: [] };
    case 'APPLY': return push(action.payload);
    case 'UNDO':
      if (!past.length) return state;
      return {
        past: past.slice(0, -1),
        present: past[past.length - 1],
        future: [present, ...future]
      };
    case 'REDO':
      if (!future.length) return state;
      return {
        past: [...past, present],
        present: future[0],
        future: future.slice(1)
      };
    default: return state;
  }
}

export default function App() {
  const [state, dispatch] = useReducer(historyReducer, {
    past: [], present: null, future: []
  });

  const { present: draft } = state || {};
  const { players = [], teams = {} } = draft || {};

  // Apply change + save to localStorage
  const apply = useCallback((updater) => {
    dispatch({ type: 'APPLY', payload: updater(draft) });
  }, [draft]);

  // Autosave on change
  useEffect(() => {
    if (draft) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
    }
  }, [draft]);

  // Load from localStorage or fetch
  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        dispatch({ type: 'INIT', payload: JSON.parse(saved) });
        return;
      } catch {
        // ignore parse errors
      }
    }

    (async () => {
      try {
        const res = await fetch('http://127.0.0.1:5001/api/get-ranked-players');
        const data = await res.json();
        const normalized = Array.isArray(data)
          ? data.map((row, i) => normalizePlayer(row, i))
          : [];
        const initialTeams = {};
        TEAM_NAMES.forEach((name) => {
          initialTeams[name] = {};
          ROSTER_SLOTS.forEach((slot) => { initialTeams[name][slot] = null; });
        });
        dispatch({
          type: 'INIT',
          payload: { players: normalized, teams: initialTeams }
        });
      } catch (err) {
        console.error('Failed to fetch initial rankings:', err);
        // still initialize with empty state so app renders
        const initialTeams = {};
        TEAM_NAMES.forEach((name) => {
          initialTeams[name] = {};
          ROSTER_SLOTS.forEach((slot) => { initialTeams[name][slot] = null; });
        });
        dispatch({
          type: 'INIT',
          payload: { players: [], teams: initialTeams }
        });
      }
    })();
  }, []);


  const handleRefresh = async () => {
    const res = await fetch('http://127.0.0.1:5001/api/get-ranked-players');
    const data = await res.json();
    const normalized = Array.isArray(data) ? data.map((row, i) => normalizePlayer(row, i)) : [];
    apply(cur => ({ ...cur, players: normalized }));
  };

  const allowDrop = (e) => e.preventDefault();

  const handleDragStart = (e, playerId) => {
    e.dataTransfer.setData('playerId', String(playerId));
  };

  const handleDrop = (e, teamName, slot) => {
    const playerId = e.dataTransfer.getData('playerId');
    apply(cur => {
      const player = cur.players.find(p => p.playerId === playerId);
      if (!player) return cur;

      const prevPlayer = cur.teams[teamName][slot];
      const nextPlayers = cur.players.filter(p => p.playerId !== playerId);
      if (prevPlayer) {
        const insertAt = nextPlayers.findIndex(p => p.rank > prevPlayer.rank);
        if (insertAt === -1) nextPlayers.push(prevPlayer);
        else nextPlayers.splice(insertAt, 0, prevPlayer);
      }

      return {
        ...cur,
        players: nextPlayers,
        teams: { ...cur.teams, [teamName]: { ...cur.teams[teamName], [slot]: player } }
      };
    });
  };

  const removeFromSlot = (teamName, slot) => {
    apply(cur => {
      const player = cur.teams[teamName][slot];
      if (!player) return cur;
      const nextTeams = { ...cur.teams, [teamName]: { ...cur.teams[teamName], [slot]: null } };
      const nextPlayers = [...cur.players];
      const insertAt = nextPlayers.findIndex(p => p.rank > player.rank);
      if (insertAt === -1) nextPlayers.push(player);
      else nextPlayers.splice(insertAt, 0, player);
      return { ...cur, teams: nextTeams, players: nextPlayers };
    });
  };

  if (!draft) {
    return <main className="p-6 text-zinc-100">Loading draft...</main>;
  }

  const bestPick = players[0] || null;

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur">
        <div className="container mx-auto max-w-7xl px-4 py-4 flex items-center justify-between gap-2">
          <h1 className="text-lg sm:text-xl font-semibold">Fantasy Draft Board</h1>
          <div className="flex gap-2">
            <button onClick={() => dispatch({ type: 'UNDO' })} disabled={!state.past.length} className="px-3 py-2 bg-zinc-800 rounded-xl disabled:opacity-50">Undo</button>
            <button onClick={() => dispatch({ type: 'REDO' })} disabled={!state.future.length} className="px-3 py-2 bg-zinc-800 rounded-xl disabled:opacity-50">Redo</button>
            <button onClick={handleRefresh} className="rounded-xl px-4 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-500">Refresh Rankings</button>
          </div>
        </div>
      </header>

      <div className="container mx-auto max-w-7xl px-4 py-6 space-y-6">
        {/* TEAM BOARD */}
        <section aria-label="Teams" className="overflow-x-auto">
          <div className="flex flex-nowrap gap-4 min-w-max pb-2">
            {TEAM_NAMES.map((team) => (
              <div key={team} className="w-64 shrink-0 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-3">
                <div className="mb-2 text-center text-sm font-semibold tracking-wide">{team}</div>
                <ul className="space-y-2">
                  {ROSTER_SLOTS.map((slot) => (
                    <li
                      key={slot}
                      onDrop={(e) => handleDrop(e, team, slot)}
                      onDragOver={allowDrop}
                      className="min-h-[44px] rounded-xl border border-zinc-800 bg-zinc-900 px-2 py-2 text-sm grid grid-cols-[56px_1fr_auto] items-center gap-2 hover:bg-zinc-800 transition"
                    >
                      <span className="text-zinc-400 font-medium">{slot}</span>
                      <span className="truncate">{teams[team][slot]?.name ?? '—'}</span>
                      {teams[team][slot] && (
                        <button onClick={() => removeFromSlot(team, slot)} className="text-red-500 text-xs">✕</button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        {/* MAIN GRID */}
        <div className="grid grid-cols-12 gap-6">
          {/* YOUR TEAM */}
          <aside className="col-span-12 md:col-span-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
            <h2 className="mb-3 text-lg font-semibold text-center">Your Team</h2>
            <ul className="space-y-2">
              {ROSTER_SLOTS.map((slot) => (
                <li key={slot} className="rounded-xl bg-zinc-900 px-3 py-2 text-sm border border-zinc-800">
                  <span className="text-zinc-400 font-medium">{slot}:</span>{' '}
                  <span className="truncate">{teams['J Ray'][slot]?.name ?? '—'}</span>
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
                    className="cursor-move rounded-xl border border-zinc-800 bg-zinc-900 p-3 hover:bg-zinc-800 transition"
                  >
                    <h3 className="font-semibold leading-tight">
                      {player.rank ? `#${player.rank} ` : ''}{player.name}
                    </h3>
                    <p className="text-xs text-zinc-300">
                      {player.position}{player.team ? ` • ${player.team}` : ''}
                    </p>
                    <p className="mt-1 text-xs text-zinc-400">
                      {player.points != null ? `Points: ${Number(player.points).toFixed(2)}` : 'Points: N/A'}
                    </p>
                  </article>
                ))
              )}
            </div>
          </section>

          {/* DRAFT SUGGESTIONS */}
          <aside className="col-span-12 md:col-span-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
            <h2 className="mb-3 text-lg font-semibold text-center">Draft Suggestions</h2>
            {bestPick ? (
              <div className="space-y-2">
                <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-3">
                  <p className="font-semibold">Pick Next: {bestPick.name}</p>
                  <p className="text-xs text-zinc-300">
                    {bestPick.position}{bestPick.team ? ` • ${bestPick.team}` : ''}
                  </p>
                </div>
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
