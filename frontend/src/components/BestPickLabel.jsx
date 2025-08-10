const BestPickLabel = ({ bestPick }) => (
  <div className="text-green-400 font-bold text-lg my-4">
    {bestPick || "Loading best pick..."}
  </div>
);

export default BestPickLabel;
