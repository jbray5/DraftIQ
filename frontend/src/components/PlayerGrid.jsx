import { useState } from 'react';
import { toPosClass } from '../utils/players';

async function askAiOpinion(payload) {
  const res = await fetch('/api/ai/opinion', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error('AI error');
  return await res.json();
}

export default function PlayerGrid({ players, handleDragStart, listRef, myTeamName, myRoster, board, meta }) {
  const [opinions, setOpinions] = useState({});
  const [loadingId, setLoadingId] = useState(null);

  async function handleAskAI(player) {
    console.log("Ask AI clicked for:", player.name, player.playerId, myTeamName);
    setLoadingId(player.playerId);
    try {
      const { opinion } = await askAiOpinion({
        player,
        myTeamName,
        myRoster,
        board,
        meta
      });
      setOpinions(prev => ({ ...prev, [player.playerId]: opinion }));
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingId(null);
    }
  }

  return (
    <section className="col-span-12 md:col-span-6 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold">Available Players</h2>
        <span className="text-xs text-zinc-400">{players.length} shown</span>
      </div>
      <div ref={listRef} className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 overflow-auto">
        {players.length === 0 ? (
          <p className="text-zinc-400">No players match your search.</p>
        ) : (
          players.map(player => {
            const ai = opinions[player.playerId];
            return (
              <article
                key={player.playerId}
                draggable
                onDragStart={e => handleDragStart(e, player.playerId)}
                className={`${toPosClass(player.position)} cursor-move rounded-xl border border-zinc-800 bg-zinc-900 p-3 hover:bg-zinc-800 transition`}
              >
                <div className="flex items-center gap-3 mb-1.5">
                  <img
                    src={player.headshot || '/placeholder-avatar.png'}
                    alt={player.name}
                    className="h-8 w-8 rounded-full object-cover bg-zinc-800 flex-shrink-0"
                    loading="lazy"
                  />
                  <h3 className="font-semibold leading-tight truncate">
                    {player.rank ? `#${player.rank} ` : ''}{player.name}
                  </h3>
                </div>
                <p className="text-xs text-zinc-300 mb-2">
                  {player.position}{player.team ? ` • ${player.team}` : ''}
                </p>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-zinc-800 border border-zinc-700">
                    Pts {player.points != null ? player.points.toFixed(2) : '—'}
                  </span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-zinc-800 border border-zinc-700">
                    ADP {player.adp ?? '—'}
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

                <button
                  className="text-[10px] px-2 py-0.5 rounded-full bg-purple-800 border border-purple-600 hover:bg-purple-700 transition"
                  onClick={() => handleAskAI(player)}
                  disabled={loadingId === player.playerId}
                >
                  {loadingId === player.playerId ? 'Thinking…' : 'Ask AI'}
                </button>

                {ai && (
                  <div className="mt-2 p-2 rounded-lg border border-zinc-700 bg-zinc-800 text-xs">
                    <div className="flex justify-between font-semibold">
                      <span>{ai.verdict}</span>
                      <span>Fit {Math.round(ai.fitScore)}</span>
                    </div>
                    <p className="mt-1">{ai.rationale}</p>
                    {ai.pros?.length > 0 && (
                      <ul className="list-disc pl-4 mt-1 text-green-400">
                        {ai.pros.map((p, i) => <li key={i}>{p}</li>)}
                      </ul>
                    )}
                    {ai.cons?.length > 0 && (
                      <ul className="list-disc pl-4 mt-1 text-red-400">
                        {ai.cons.map((c, i) => <li key={i}>{c}</li>)}
                      </ul>
                    )}
                  </div>
                )}
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}
