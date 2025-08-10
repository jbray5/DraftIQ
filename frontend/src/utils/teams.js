import { normText } from './text';

export function buildEmptyTeams(names, slots) {
  const t = {};
  names.forEach(n => {
    t[n] = {};
    slots.forEach(s => (t[n][s] = null));
  });
  return t;
}

export function normalizeName(s = '') {
  return normText(s).replace(/ /g, '');
}

export function migrateTeams(savedTeams, names, slots) {
  const empty = buildEmptyTeams(names, slots);
  if (!savedTeams || typeof savedTeams !== 'object') return empty;

  const nameIndex = Object.fromEntries(names.map(n => [normalizeName(n), n]));
  Object.entries(savedTeams).forEach(([oldName, slotsObj]) => {
    const target = nameIndex[normalizeName(oldName)];
    if (target) empty[target] = { ...empty[target], ...slotsObj };
  });
  return empty;
}
