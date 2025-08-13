import { ROSTER_SLOTS } from '../constants';
import { toPosClass } from '../utils/players';

export default function YourTeam({ myTeam, getHeadshot }) {
  return (
    <aside className="col-span-12 md:col-span-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
      <h2 className="mb-3 text-lg font-semibold text-center">Your Team</h2>
      <ul className="space-y-2">
        {ROSTER_SLOTS.map(slot => {
          const p = myTeam?.[slot];
          const img = p ? getHeadshot?.(p) : null;
          const posClass = toPosClass(p?.position);
          return (
            <li key={slot} className={`${posClass} rounded-xl bg-zinc-900 px-3 py-2 text-sm border border-zinc-800 flex items-center gap-2`}>
              {p ? (
                <img src={img || '/placeholder-avatar.png'} alt={p.name} className="h-6 w-6 rounded-full object-cover bg-zinc-800" loading="lazy" />
              ) : (
                <span className="h-6 w-6 rounded-full bg-zinc-800 inline-block" />
              )}
              <span className="text-zinc-400 font-medium">{slot}:</span>
              <span className="truncate">{p?.name ?? '—'}</span>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
