// utils/loadPlayerData.js
import Papa from 'papaparse';
import playerCsv from '../data/processed/merged_data_v2.csv?url';

export const loadPlayerData = async () => {
  const response = await fetch(playerCsv);
  const text = await response.text();
  return new Promise((resolve) => {
    Papa.parse(text, {
      header: true,
      dynamicTyping: true,
      complete: (results) => {
        resolve(results.data);
      },
    });
  });
};
