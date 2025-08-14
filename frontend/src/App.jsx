// App.jsx
import { TEAM_NAMES, ROSTER_SLOTS } from './constants';
import useDraftData from './hooks/useDraftData';
import useSearch from './hooks/useSearch';
import useDragResize from './hooks/useDragResize';
import useHeadshots from "./hooks/useHeadshots";

import Header from './components/Header';
import TeamBoard from './components/TeamBoard';
import PlayerGrid from './components/PlayerGrid';
import Suggestions from './components/Suggestions';
import YourTeam from './components/YourTeam';

import './App.css';


export default function App() {
  const { draft, apply, undo, redo, refresh, pastLength, futureLength } = useDraftData();
  const { query, setQuery, searchRef, listRef, filteredPlayers } = useSearch(draft?.players ?? []);
  const { boardHeight, startDrag } = useDragResize();
  const { getHeadshot, isReady: headshotsReady } = useHeadshots(); // <— add

  if (!draft) return <main className="p-6 text-zinc-100">Loading draft...</main>;

  const handleDragStart = (e, playerId) => e.dataTransfer.setData('playerId', String(playerId));

  const handleDrop = (e, teamName, slot) => {
    const playerId = e.dataTransfer.getData('playerId');
    apply(cur => {
      const player = cur.players.find(p => p?.playerId === playerId);
      if (!player) return cur;
      const teamSlots = cur.teams[teamName] ?? {};
      const prevPlayer = teamSlots[slot];
      const nextPlayers = cur.players.filter(p => p?.playerId !== playerId);
      if (prevPlayer) {
        const insertAt = nextPlayers.findIndex(p => (p?.rank ?? Infinity) > (prevPlayer?.rank ?? Infinity));
        insertAt === -1 ? nextPlayers.push(prevPlayer) : nextPlayers.splice(insertAt, 0, prevPlayer);
      }
      return { ...cur, players: nextPlayers, teams: { ...cur.teams, [teamName]: { ...teamSlots, [slot]: { ...player } } } };
    });
  };

  const removeFromSlot = (teamName, slot) => {
    apply(cur => {
      const player = cur.teams[teamName]?.[slot];
      if (!player) return cur;
      const nextTeams = { ...cur.teams, [teamName]: { ...cur.teams[teamName], [slot]: null } };
      const nextPlayers = [...cur.players];
      const insertAt = nextPlayers.findIndex(p => (p?.rank ?? Infinity) > (player?.rank ?? Infinity));
      insertAt === -1 ? nextPlayers.push(player) : nextPlayers.splice(insertAt, 0, player);
      return { ...cur, teams: nextTeams, players: nextPlayers };
    });
  };

    const myTeamName = "JRay";
    const myTeam = draft.teams?.[myTeamName] ?? {};

    return (
      <main className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col">
        <Header
          query={query}
          setQuery={setQuery}
          searchRef={searchRef}
          totalPlayers={draft.players.length}
          shownPlayers={filteredPlayers.length}
          undo={undo}
          redo={redo}
          pastLength={pastLength}
          futureLength={futureLength}
          refresh={refresh}
        />
        <TeamBoard
          teams={draft.teams}
          boardHeight={boardHeight}
          startDrag={startDrag}
          handleDrop={handleDrop}
          removeFromSlot={removeFromSlot}
          getHeadshot={getHeadshot} // <— pass down
        />
        <div className="container mx-auto max-w-7xl px-4 py-6 space-y-6 flex-1 overflow-auto">
          <div className="grid grid-cols-12 gap-6">
            <YourTeam myTeam={myTeam} getHeadshot={getHeadshot} />
            <PlayerGrid
              players={filteredPlayers}
              handleDragStart={handleDragStart}
              listRef={listRef}
              myTeamName={myTeamName}               // <-- now passed
              myRoster={draft.teams[myTeamName]}    // <-- pass your roster object
              board={draft.teams}                   // <-- pass the full draft board
              meta={{
                round: Math.ceil((draft.pickNumber ?? 1) / TEAM_NAMES.length), // adjust if you store this differently
                pickNumber: draft.pickNumber ?? 1,
                pickInRound: ((draft.pickNumber ?? 1) - 1) % TEAM_NAMES.length + 1,
                teams: TEAM_NAMES.length
              }}
              getHeadshot={getHeadshot}
            />
            <Suggestions players={filteredPlayers} getHeadshot={getHeadshot} />
          </div>
        </div>
      </main>
    );
  }
