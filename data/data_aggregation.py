#data_aggregation.py
import requests
import pandas as pd
import os

# Your API key for SportsData.io
API_KEY = '8d4ad1f719b74a8da51fb2f981a60918'
BASE_API_URL = 'https://api.sportsdata.io/v3/nfl/'

# Define the base directory for your project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DATA_DIR = os.path.join(BASE_DIR, 'data', 'raw')

# Function to fetch data from a given endpoint
def fetch_data(endpoint, params=None):
    url = BASE_API_URL + endpoint
    headers = {'Ocp-Apim-Subscription-Key': API_KEY}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()  # Raise an exception for HTTP errors
    return response.json()

# Function to save data to a CSV file
def save_data_to_csv(data, filename):
    df = pd.DataFrame(data)
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    df.to_csv(filename, index=False)
    print(f"Data saved to {filename}")
    
# Example: Integrating with draft strategy modeling
def develop_strategy(df_draft, df_analyzed):
    # Model strategies based on historical success
    # This could include identifying sleeper picks or recognizing early runs on positions
    strategy_recommendations = []
    # Add logic to populate strategy_recommendations
    return strategy_recommendations


def main():
    # Update the season and week parameters
    season = '2024'
    
    # Fetch player projections for the 2024 season
    try:
        player_projections = fetch_data(f'projections/json/PlayerSeasonProjectionStats/{season}')
        save_data_to_csv(player_projections, os.path.join(RAW_DATA_DIR, f'player_projections_{season}.csv'))
    except requests.exceptions.HTTPError as e:
        print(f"Failed to fetch player projections data: {e}")

if __name__ == "__main__":
    main()
