#api.py
from flask import Flask, send_file, jsonify
from data.update_data import update_data
import os
import pandas as pd
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_CSV_PATH = os.path.join(BASE_DIR, 'data', 'processed', 'merged_data_v2.csv')

@app.route('/api/refresh-data', methods=["POST"])
def refresh_data():
    try:
        update_data()
        return jsonify({"status": "success", "message": "Data refreshed."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/players', methods=["GET"])
def get_players():
    if not os.path.exists(PROCESSED_CSV_PATH):
        return jsonify({"error": "File not found."}), 404
    return send_file(PROCESSED_CSV_PATH, mimetype='text/csv')


@app.route('/api/get-ranked-players', methods=['GET'])
def get_ranked_players():
    df = pd.read_csv('data/processed/merged_data_v2.csv')  # adjust if needed
    print(df.head().to_dict(orient='records'))  # <--- TEMP DEBUG
    return df.to_json(orient='records')




# 👇 This is what starts the server!
if __name__ == "__main__":
    app.run(debug=True)
