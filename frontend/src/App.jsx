import React from 'react';

export default function App() {
  // Trigger the backend to refresh data
  const handleRefresh = async () => {
    try {
      const response = await fetch('http://127.0.0.1:5000/api/refresh-data');
      const result = await response.json();
      alert(result.message || 'Data refresh triggered!');
    } catch (error) {
      console.error('Error refreshing data:', error);
      alert('Failed to refresh data.');
    }
  };

  return (
    <div className="h-screen bg-gray-900 text-white grid grid-cols-12 gap-4 p-4 font-sans">
      {/* Sidebar: Your Team */}
      <aside className="col-span-2 bg-gray-800 p-4 rounded-xl shadow-md">
        <h2 className="text-xl font-bold mb-4">Your Team</h2>
        <ul className="space-y-2">
          <li className="bg-gray-700 p-2 rounded">QB: _</li>
          <li className="bg-gray-700 p-2 rounded">RB1: _</li>
          <li className="bg-gray-700 p-2 rounded">RB2: _</li>
          <li className="bg-gray-700 p-2 rounded">WR1: _</li>
          <li className="bg-gray-700 p-2 rounded">WR2: _</li>
          <li className="bg-gray-700 p-2 rounded">TE: _</li>
          <li className="bg-gray-700 p-2 rounded">FLEX: _</li>
          <li className="bg-gray-700 p-2 rounded">IDP: _</li>
          <li className="bg-gray-700 p-2 rounded">Bench: _</li>
        </ul>
      </aside>

      {/* Main Draft Board */}
      <main className="col-span-7 bg-gray-800 p-4 rounded-xl shadow-md overflow-y-auto">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold">Available Players</h2>
          <button
            onClick={handleRefresh}
            className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded shadow"
          >
            Refresh Data
          </button>
        </div>

        <div className="grid grid-cols-3 gap-4">
          {/* Sample player cards */}
          <div className="bg-gray-700 p-3 rounded hover:bg-gray-600 transition">
            <p className="font-bold">Justin Jefferson</p>
            <p className="text-sm text-gray-300">WR - MIN</p>
          </div>
          <div className="bg-gray-700 p-3 rounded hover:bg-gray-600 transition">
            <p className="font-bold">Christian McCaffrey</p>
            <p className="text-sm text-gray-300">RB - SF</p>
          </div>
          {/* Repeat as needed or map from data */}
        </div>
      </main>

      {/* Draft Suggestions */}
      <section className="col-span-3 bg-gray-800 p-4 rounded-xl shadow-md">
        <h2 className="text-xl font-bold mb-4">Draft Suggestions</h2>
        <div className="space-y-2">
          <div className="bg-gray-700 p-2 rounded">
            <p className="font-bold">Pick Next: Bijan Robinson</p>
            <p className="text-sm text-gray-300">RB - ATL</p>
          </div>
          <div className="bg-gray-700 p-2 rounded">
            <p className="font-bold">Also Consider: Amon-Ra St. Brown</p>
            <p className="text-sm text-gray-300">WR - DET</p>
          </div>
        </div>
      </section>
    </div>
  );
}
