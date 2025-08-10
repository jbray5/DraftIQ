import { useDrop } from 'react-dnd';

const RosterSlot = ({ slotName, allowedPositions, assignedPlayer, onDrop }) => {
  const [{ isOver, canDrop }, drop] = useDrop(() => ({
    accept: 'PLAYER',
    drop: (player) => onDrop(slotName, player),
    canDrop: (player) => allowedPositions.includes(player.Position_stats),
    collect: monitor => ({
      isOver: monitor.isOver(),
      canDrop: monitor.canDrop(),
    }),
  }));

  const borderColor = isOver ? (canDrop ? 'border-green-500' : 'border-red-500') : 'border-gray-500';

  return (
    <div
      ref={drop}
      className={`w-64 h-16 flex items-center justify-center border-2 ${borderColor} text-white rounded mb-2`}
    >
      {assignedPlayer ? assignedPlayer.Name_stats : slotName}
    </div>
  );
};

export default RosterSlot;
