import React, { useEffect, useState } from 'react';


const TEAM_NAMES = [
  'Josh', 'Mac', 'D-Put', 'Jeff', 'Wester', 'J Ray',
  'Taylor', 'Will', 'Spivey', 'CJ', 'Walk', 'Thom'
];

const ROSTER_SLOTS = ['QB', 'RB1', 'RB2', 'WR1', 'WR2', 'TE', 'FLEX', 'IDP', 'K', 'Bench'];

export default function App() {
  const [players, setPlayers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [bestPick, setBestPick] = useState(null);

  const [teams, setTeams] = useState(() => {
    const initial = {};
    TEAM_NAMES.forEach((name) => {
      initial[name] = {};
      ROSTER_SLOTS.forEach((slot) => {
        initial[name][slot] = null;
      });
    });
    return initial;
  });

  useEffect(() => {
    const fetchPlayers = async () => {
      try {
        const res = await fetch('http://127.0.0.1:5000/api/get-ranked-players');
        const data = await res.json();
        setPlayers(data || []);
        setBestPick((data && data[0]) || null);
      } catch (err) {
        console.error('Failed to fetch player data:', err);
      }
    };
    fetchPlayers();
  }, []);

  const handleRefresh = async () => {
    setLoading(true);
    try {
      const response = await fetch('http://127.0.0.1:5000/api/refresh-data', { method: 'POST' });
      const result = await response.json();
      alert(result.message || 'Data refresh triggered!');
    } catch (error) {
      console.error('Error refreshing data:', error);
      alert('Failed to refresh data.');
    } finally {
      setLoading(false);
    }
  };

  const allowDrop = (e) => e.preventDefault();

  const handleDragStart = (e, playerId) => {
    e.dataTransfer.setData('playerId', String(playerId));
  };

  const handleDrop = (e, teamName, slot) => {
    const playerId = e.dataTransfer.getData('playerId');
    const player = players.find((p) => String(p.PlayerID) === String(playerId));
    if (!player) return;

    setTeams((prev) => ({
      ...prev,
      [teamName]: {
        ...prev[teamName],
        [slot]: player,
      },
    }));

    setPlayers((prev) => prev.filter((p) => String(p.PlayerID) !== String(playerId)));
  };

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* Header / toolbar */}
      <header className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur">
        <div className="container mx-auto max-w-7xl px-4 py-4 flex items-center justify-between">
          <h1 className="text-lg sm:text-xl font-semibold">Fantasy Draft Board</h1>
          <button
            onClick={handleRefresh}
            className="rounded-xl px-4 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-500 active:bg-blue-600 transition disabled:opacity-50"
            disabled={loading}
          >
            {loading ? 'Refreshing…' : 'Refresh Data'}
          </button>
        </div>
      </header>

      <div className="container mx-auto max-w-7xl px-4 py-6 space-y-6">
        {/* TEAM BOARD: horizontal scroll cards */}
        <section aria-label="Teams" className="overflow-x-auto">
          <div className="flex flex-nowrap gap-4 min-w-max pb-2">
            {TEAM_NAMES.map((team) => (
              <div
                key={team}
                className="w-64 shrink-0 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-3"
              >
                <div className="mb-2 text-center text-sm font-semibold tracking-wide">
                  {team}
                </div>
                <ul className="space-y-2">
                  {ROSTER_SLOTS.map((slot) => (
                    <li
                      key={slot}
                      onDrop={(e) => handleDrop(e, team, slot)}
                      onDragOver={allowDrop}
                      className="min-h-[44px] rounded-xl border border-zinc-800 bg-zinc-900 px-2 py-2 text-sm grid grid-cols-[56px_1fr] items-center gap-2 hover:bg-zinc-800 transition"
                    >
                      <span className="text-zinc-400 font-medium">{slot}</span>
                      <span className="truncate">
                        {teams[team][slot]?.Name_stats || teams[team][slot]?.Name || '—'}
                      </span>
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
                  <span className="truncate">
                    {teams['J Ray'][slot]?.Name_stats ||
                      teams['J Ray'][slot]?.Name ||
                      '—'}
                  </span>
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
                players.map((player) => {
                  const name = player.Name_stats || player.Name || 'Unknown';
                  const pos = player.Position_stats || player.Position || '';
                  const team = player.Team_stats || player.Team || '';
                  const score =
                    typeof player.Composite_Score === 'number'
                      ? player.Composite_Score.toFixed(2)
                      : 'N/A';

                  return (
                    <article
                      key={player.PlayerID}
                      draggable
                      onDragStart={(e) => handleDragStart(e, player.PlayerID)}
                      className="cursor-move rounded-xl border border-zinc-800 bg-zinc-900 p-3 hover:bg-zinc-800 transition"
                    >
                      <h3 className="font-semibold leading-tight">{name}</h3>
                      <p className="text-xs text-zinc-300">{pos}{team ? ` • ${team}` : ''}</p>
                      <p className="mt-1 text-xs text-zinc-400">Score: {score}</p>
                    </article>
                  );
                })
              )}
            </div>
          </section>

          {/* DRAFT SUGGESTIONS */}
          <aside className="col-span-12 md:col-span-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
            <h2 className="mb-3 text-lg font-semibold text-center">Draft Suggestions</h2>
            {bestPick ? (
              <div className="space-y-2">
                <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-3">
                  <p className="font-semibold">
                    Pick Next: {bestPick.Name_stats || bestPick.Name || 'Unknown'}
                  </p>
                  <p className="text-xs text-zinc-300">
                    {(bestPick.Position_stats || bestPick.Position || '')}
                    {bestPick.Team_stats || bestPick.Team ? (
                      <> • {bestPick.Team_stats || bestPick.Team}</>
                    ) : null}
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
