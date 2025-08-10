import RosterSlot from './RosterSlot';

const TeamRoster = ({ teamName, slots, roster, onDrop }) => (
  <div className="mr-10 mb-6">
    <h3 className="text-red-400 font-bold mb-2">{teamName} Roster</h3>
    {Object.entries(slots).map(([slotName, allowedPositions]) => (
      <RosterSlot
        key={`${teamName}_${slotName}`}
        slotName={slotName}
        allowedPositions={allowedPositions}
        assignedPlayer={roster[slotName]}
        onDrop={(slot, player) => onDrop(teamName, slot, player)}
      />
    ))}
  </div>
);

export default TeamRoster;
