import { useDrag } from 'react-dnd';

const DraggablePlayer = ({ player, isAssigned }) => {
  const [{ isDragging }, drag] = useDrag(() => ({
    type: 'PLAYER',
    item: { ...player },
    collect: monitor => ({
      isDragging: monitor.isDragging(),
    }),
  }));

  return (
    <div
      ref={drag}
      className={`p-2 rounded mb-2 text-white text-sm cursor-pointer ${
        isAssigned ? 'bg-gray-700 line-through opacity-50' : 'bg-gray-800 hover:bg-gray-700'
      }`}
    >
      {player.Name_stats} ({player.Position_stats}) - Score: {player.Composite_Score.toFixed(2)}
    </div>
  );
};

export default DraggablePlayer;
