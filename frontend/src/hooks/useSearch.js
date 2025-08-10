// hooks/useSearch.js
import { useState, useRef, useEffect, useMemo } from 'react';
import { matchesPlayer } from '../utils/players';
import { normText } from '../utils/text';

export default function useSearch(players) {
  const [query, setQuery] = useState('');
  const searchRef = useRef(null);
  const listRef = useRef(null);

  useEffect(() => {
    const onKey = e => {
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (!normText(query)) listRef.current?.scrollTo({ top: 0, behavior: 'instant' });
  }, [query]);

  const filteredPlayers = useMemo(() => players.filter(p => matchesPlayer(p, query)), [players, query]);

  return { query, setQuery, searchRef, listRef, filteredPlayers };
}
