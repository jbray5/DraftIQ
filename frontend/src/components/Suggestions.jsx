import { toPosClass } from '../utils/players';

export default function Suggestions({ players }) {
  const top = [...players]
    .sort((a, b) => (b.compositeScore ?? 0) - (a.compositeScore ?? 0))
    .slice(0, 5);

  return (
    <aside className="col-span-12 md:col-span-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
      <h2 className="mb-3 text-lg font-semibold text-center">Draft Suggestions</h2>
      {top.length ? (
        <div className="space-y-2">
          {top.map((p, i) => {
            return (
              <div
                key={p.playerId}
                className={`${toPosClass(p.position)} rounded-xl border border-zinc-800 bg-zinc-900 p-3`}
              >
                <div className="flex items-center gap-3">
                  <img
                    src={p.headshot || '/placeholder-avatar.png'}
                    alt={p.name}
                    className="h-7 w-7 rounded-full object-cover bg-zinc-800"
                    loading="lazy"
                  />
                  <div className="min-w-0">
                    <p className="font-semibold truncate">
                      #{i + 1} {p.name}{' '}
                      <span className="text-zinc-400 text-xs">
                        ({p.compositeScore?.toFixed(3) ?? '—'})
                      </span>
                    </p>
                    <p className="text-xs text-zinc-300">
                      {p.position}{p.team ? ` • ${p.team}` : ''}
                    </p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-zinc-400 text-sm">No suggestion available.</p>
      )}
    </aside>
  );
}
