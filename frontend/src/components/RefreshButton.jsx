import { useState } from 'react';
import axios from 'axios';

export default function RefreshButton() {
  const [status, setStatus] = useState(null);

    const handleRefresh = async () => {
    try {
      const response = await fetch('http://127.0.0.1:5000/api/refresh-data', {
        method: 'POST',
      });
      if (response.ok) {
        alert('Data refreshed!');
        } else {
        alert('Failed to refresh data.');
        }
      } catch (error) {
        console.error('Error refreshing data:', error);
        alert('Error refreshing data.');
      }
    };


  return (
    <div className="mb-4">
      <button
        onClick={handleRefresh}
        className="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded"
      >
        Refresh Player Data
      </button>
      {status && <p className="mt-2 text-sm text-gray-600">{status}</p>}
    </div>
  );
}
