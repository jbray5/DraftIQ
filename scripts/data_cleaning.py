import pandas as pd
import os

# Define paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DATA_DIR = os.path.join(BASE_DIR, 'data', 'raw')
PROCESSED_DATA_DIR = os.path.join(BASE_DIR, 'data', 'processed')

# Load raw data
player_data = pd.read_csv(os.path.join(RAW_DATA_DIR, 'player_data.csv'))

# Inspect the data columns
print("Columns in the dataset:")
print(player_data.columns)

# Filter out inactive players
player_data = player_data[player_data['Status'] == 'Active']

# Select relevant columns
columns_to_keep = [
    'PlayerID', 'FirstName', 'LastName', 'Team', 'Position',
    'AverageDraftPosition', 'InjuryStatus', 'Status'
]
filtered_data = player_data[columns_to_keep]

# Handle any scrambled data in 'InjuryStatus' (if applicable)
# You can either drop or handle these rows based on your strategy
# Here, we'll keep them but mark them clearly
filtered_data['InjuryStatus'] = filtered_data['InjuryStatus'].replace('Scrambled', 'Unknown')

# Save cleaned data
os.makedirs(PROCESSED_DATA_DIR, exist_ok=True)
cleaned_data_path = os.path.join(PROCESSED_DATA_DIR, 'cleaned_player_data.csv')
filtered_data.to_csv(cleaned_data_path, index=False)
print(f"Cleaned data saved to {cleaned_data_path}")
