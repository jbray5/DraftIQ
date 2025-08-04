import pandas as pd

def suggest_next_pick(csv_path='data/processed/merged_data_v2.csv', team_name='JRay'):
    df = pd.read_csv(csv_path)
    df = df.sort_values(by='Composite_Score', ascending=False)
    
    # Simplified logic (based on your GUI logic)
    for _, player in df.iterrows():
        if player['Position_stats'] in ['QB', 'RB', 'WR', 'TE']:
            return {
                "name": player['Name_stats'],
                "position": player['Position_stats'],
                "composite_score": round(player['Composite_Score'], 2)
            }
    return {"name": "None", "position": "-", "composite_score": 0.0}
