#data_analysis.py
import pandas as pd
import os

# Define paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DATA_DIR = os.path.join(BASE_DIR, 'data', 'raw')
PROCESSED_DATA_DIR = os.path.join(BASE_DIR, 'data', 'processed')

# Load the draft data from the CSV file
draft_data_path = os.path.join(RAW_DATA_DIR, '2023_draft.csv')
df_draft = pd.read_csv(draft_data_path)

# Display the first few rows of the DataFrame to ensure it's loaded correctly
print(df_draft.head())

# Basic analysis: Count the number of picks per position
position_counts = df_draft['Position'].value_counts()
print("Number of picks per position:")
print(position_counts)

# Analysis: Average pick position by position
average_pick_by_position = df_draft.groupby('Position')['Pick'].mean()
print("Average pick position by position:")
print(average_pick_by_position)

# Save the analyzed data
analyzed_data_path = os.path.join(PROCESSED_DATA_DIR, '2023_draft_analyzed.csv')
df_draft.to_csv(analyzed_data_path, index=False)
