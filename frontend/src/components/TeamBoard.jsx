import { TEAM_NAMES, ROSTER_SLOTS } from '../constants';
import { toPosClass } from '../utils/players';

export default function TeamBoard({ teams, boardHeight, startDrag, handleDrop, removeFromSlot, getHeadshot }) {
  const allowDrop = e => e.preventDefault();

  return (
    <>
      <section style={{ height: boardHeight }} className="overflow-x-auto overflow-y-auto">
        <div className="flex flex-nowrap gap-4 min-w-max pb-2">
          {TEAM_NAMES.map(team => {
            const teamSlots = teams?.[team] ?? {};
            return (
              <div key={team} className="w-64 shrink-0 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-3">
                <div className="mb-2 text-center text-sm font-semibold">{team}</div>
                <ul className="space-y-2">
                  {ROSTER_SLOTS.map(slot => {
                    const p = teamSlots?.[slot];
                    const posClass = toPosClass(p?.position);
                    const img = p ? getHeadshot?.(p) : null;
                    return (
                      <li
                        key={slot}
                        onDrop={e => handleDrop(e, team, slot)}
                        onDragOver={allowDrop}
                        className={`${posClass} min-h-[44px] rounded-xl border border-zinc-800 bg-zinc-900
                          px-2 py-2 text-sm grid grid-cols-[24px_1fr_auto] items-center gap-2 hover:bg-zinc-800 transition`}
                      >
                        {p ? (
                          <img
                            src={img || '/placeholder-avatar.png'}
                            alt={p.name}
                            className="h-6 w-6 rounded-full object-cover bg-zinc-800"
                            loading="lazy"
                          />
                        ) : (
                          <span className="h-6 w-6 rounded-full bg-zinc-800 inline-block" />
                        )}
                        <div className="truncate">
                          <span className="text-zinc-400 font-medium mr-1">{slot}</span>
                          <span className="truncate">{p?.name ?? '—'}</span>
                        </div>
                        {p && (
                          <button onClick={() => removeFromSlot(team, slot)} className="text-red-500 text-xs">✕</button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
      </section>
      <div onMouseDown={startDrag} className="h-2 cursor-row-resize bg-zinc-700 hover:bg-zinc-600" />
    </>
  );
}
