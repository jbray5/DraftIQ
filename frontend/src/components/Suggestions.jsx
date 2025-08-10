import { toPosClass } from '../utils/players';

export default function Suggestions({ players }) {
  return (
    <aside className="col-span-12 md:col-span-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
      <h2 className="mb-3 text-lg font-semibold text-center">Draft Suggestions</h2>
      {players.length > 0 ? (
        <div className="space-y-2">
          {[...players]
            .sort((a, b) => (b.compositeScore ?? 0) - (a.compositeScore ?? 0))
            .slice(0, 5)
            .map((p, i) => (
              <div
                key={p.playerId}
                className={`${toPosClass(p.position)} rounded-xl border border-zinc-800 bg-zinc-900 p-3`}
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
  );
}
