import { toPosClass } from '../utils/players';

export default function PlayerGrid({ players, handleDragStart, listRef }) {
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
          players.map(player => (
            <article
              key={player.playerId}
              draggable
              onDragStart={e => handleDragStart(e, player.playerId)}
              className={`${toPosClass(player.position)} cursor-move rounded-xl border border-zinc-800 bg-zinc-900 p-3 hover:bg-zinc-800 transition`}
            >
              <h3 className="font-semibold leading-tight">
                {player.rank ? `#${player.rank} ` : ''}{player.name}
              </h3>
              <p className="text-xs text-zinc-300 mb-2">
                {player.position}{player.team ? ` • ${player.team}` : ''}
              </p>
              <div className="flex flex-wrap gap-1.5">
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
            </article>
          ))
        )}
      </div>
    </section>
  );
}
