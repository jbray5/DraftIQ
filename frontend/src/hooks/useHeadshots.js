import { useEffect, useMemo, useState } from "react";

const HEADSHOTS_ENDPOINT =
  "https://api.sportsdata.io/v3/nfl/headshots/json/Headshots?key=7c19ff807b5a4ac29c766d4bf73d92bf";
const PLAYERS_ENDPOINT =
  "https://api.sportsdata.io/v3/nfl/scores/json/Players?key=7c19ff807b5a4ac29c766d4bf73d92bf";

const CACHE_KEY = "ffd_headshots_v2";
const PLAYERS_CACHE_KEY = "ffd_players_v1";
const TTL_MS = 10 * 60 * 60 * 1000;

const norm = (s) => (s || "").toString().trim().toLowerCase();
const pickUrl = (row) =>
  row?.Url || row?.UrlHiRes || row?.HighResImageUrl || row?.ImageUrl || row?.PhotoUrl || row?.HeadshotUrl || null;

const cdnFromId = (pid) =>
  pid ? `https://cdn.sportsdata.io/headshots/nfl/low/${pid}.png` : null;

export default function useHeadshots() {
  const [heads, setHeads] = useState(null);
  const [players, setPlayers] = useState(null);

  // headshots list
  useEffect(() => {
    const go = async () => {
      try {
        const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
        if (cached && Date.now() < cached.exp) return setHeads(cached.data);
      } catch {}
      try {
        const res = await fetch(HEADSHOTS_ENDPOINT);
        const data = await res.json();
        setHeads(data);
        localStorage.setItem(CACHE_KEY, JSON.stringify({ exp: Date.now() + TTL_MS, data }));
      } catch { setHeads([]); }
    };
    go();
  }, []);

  // players list (for PhotoUrl fallback)
  useEffect(() => {
    const go = async () => {
      try {
        const cached = JSON.parse(localStorage.getItem(PLAYERS_CACHE_KEY) || "null");
        if (cached && Date.now() < cached.exp) return setPlayers(cached.data);
      } catch {}
      try {
        const res = await fetch(PLAYERS_ENDPOINT);
        const data = await res.json();
        setPlayers(data);
        localStorage.setItem(PLAYERS_CACHE_KEY, JSON.stringify({ exp: Date.now() + TTL_MS, data }));
      } catch { setPlayers([]); }
    };
    go();
  }, []);

  const { byId, byNameTeam, photoById } = useMemo(() => {
    const byId = new Map();
    const byNameTeam = new Map();
    (heads || []).forEach((r) => {
      const url = pickUrl(r);
      if (!url) return;
      const pid = r?.PlayerID != null ? String(r.PlayerID) : null;
      if (pid) byId.set(pid, url);
      byNameTeam.set(`${norm(r?.Name)}|${norm(r?.Team)}`, url);
    });

    const photoById = new Map();
    (players || []).forEach((p) => {
      const pid = p?.PlayerID != null ? String(p.PlayerID) : null;
      const photo = p?.PhotoUrl || null;
      if (pid && photo) photoById.set(pid, photo);
    });

    return { byId, byNameTeam, photoById };
  }, [heads, players]);

  function getHeadshot(player) {
    if (!player) return null;
    const pid = player.playerId ? String(player.playerId) : null;

    return (
      (pid && byId.get(pid)) ||
      (pid && photoById.get(pid)) ||
      byNameTeam.get(`${norm(player.name)}|${norm(player.team)}`) ||
      cdnFromId(pid) ||
      null
    );
  }

  return { getHeadshot, isReady: !!heads };
}
