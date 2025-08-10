import { ROSTER_SLOTS } from '../constants';
import { toPosClass } from '../utils/players';

export default function YourTeam({ myTeam }) {
  return (
    <aside className="col-span-12 md:col-span-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
      <h2 className="mb-3 text-lg font-semibold text-center">Your Team</h2>
      <ul className="space-y-2">
        {ROSTER_SLOTS.map(slot => {
          const posClass = toPosClass(myTeam?.[slot]?.position);
          return (
            <li
              key={slot}
              className={`${posClass} rounded-xl bg-zinc-900 px-3 py-2 text-sm border border-zinc-800`}
            >
              <span className="text-zinc-400 font-medium">{slot}:</span>{' '}
              <span className="truncate">{myTeam?.[slot]?.name ?? '—'}</span>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
