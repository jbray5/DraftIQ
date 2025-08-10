// hooks/useDraftData.js
import { useReducer, useEffect, useCallback } from 'react';
import { TEAM_NAMES, ROSTER_SLOTS, STORAGE_KEY } from '../constants';
import { buildEmptyTeams, migrateTeams } from '../utils/teams';
import { normalizePlayer, calculateDraftMetrics } from '../utils/players';

function historyReducer(state, action) {
  const { past, present, future } = state;
  const push = next => ({ past: [...past, present], present: next, future: [] });
  switch (action.type) {
    case 'INIT': return { past: [], present: action.payload, future: [] };
    case 'APPLY': return push(action.payload);
    case 'UNDO': return past.length ? { past: past.slice(0, -1), present: past.at(-1), future: [present, ...future] } : state;
    case 'REDO': return future.length ? { past: [...past, present], present: future[0], future: future.slice(1) } : state;
    default: return state;
  }
}

export default function useDraftData() {
  const [state, dispatch] = useReducer(historyReducer, { past: [], present: null, future: [] });

  const draft = state.present;
  const pastLength = state.past.length;
  const futureLength = state.future.length;

  const apply = useCallback(updater => draft && dispatch({ type: 'APPLY', payload: updater(draft) }), [draft]);
  const undo = () => dispatch({ type: 'UNDO' });
  const redo = () => dispatch({ type: 'REDO' });

  useEffect(() => { draft && localStorage.setItem(STORAGE_KEY, JSON.stringify(draft)); }, [draft]);

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        const withMetrics = calculateDraftMetrics(parsed.players || []);
        const migratedTeams = migrateTeams(parsed.teams, TEAM_NAMES, ROSTER_SLOTS);
        const sortedByADP = [...withMetrics].sort((a, b) => (a?.adp ?? 9999) - (b?.adp ?? 9999));
        dispatch({ type: 'INIT', payload: { players: sortedByADP, teams: migratedTeams } });
        return;
      } catch { localStorage.removeItem(STORAGE_KEY); }
    }
    (async () => {
      try {
        const res = await fetch('http://127.0.0.1:5001/api/get-ranked-players');
        const data = await res.json();
        const normalized = Array.isArray(data) ? data.map((row, i) => normalizePlayer(row, i)) : [];
        const withMetrics = calculateDraftMetrics(normalized);
        const sortedByADP = [...withMetrics].sort((a, b) => (a?.adp ?? 9999) - (b?.adp ?? 9999));
        dispatch({ type: 'INIT', payload: { players: sortedByADP, teams: buildEmptyTeams(TEAM_NAMES, ROSTER_SLOTS) } });
      } catch {
        dispatch({ type: 'INIT', payload: { players: [], teams: buildEmptyTeams(TEAM_NAMES, ROSTER_SLOTS) } });
      }
    })();
  }, []);

  const refresh = async () => {
    try {
      const res = await fetch('http://127.0.0.1:5001/api/get-ranked-players');
      const data = await res.json();
      const normalized = Array.isArray(data) ? data.map((row, i) => normalizePlayer(row, i)) : [];
      const withMetrics = calculateDraftMetrics(normalized);
      const sortedByADP = [...withMetrics].sort((a, b) => (a?.adp ?? 9999) - (b?.adp ?? 9999));
      apply(cur => ({ ...cur, players: sortedByADP }));
    } catch {}
  };

  return { draft, apply, undo, redo, refresh, pastLength, futureLength };
}
