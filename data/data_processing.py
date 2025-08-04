#data_processing.py
import os
import pandas as pd

def calculate_replacement_values(player_data, league_settings):
    replacement_values = {}
    for position, starters_needed in league_settings.items():
        position_players = player_data[player_data['Position_stats'] == position]
        position_players = position_players.sort_values(by='FantasyPointsPPR_proj', ascending=False)

        print(f"Position: {position}, Available Players: {len(position_players)}")  # Debugging

        replacement_index = starters_needed * league_settings['num_teams']
        if len(position_players) >= replacement_index:
            replacement_value = position_players.iloc[replacement_index - 1]['FantasyPointsPPR_proj']
        elif len(position_players) > 0:
            replacement_value = position_players.iloc[-1]['FantasyPointsPPR_proj']
        else:
            print(f"Warning: No players found for position {position}. Defaulting replacement value to 0.")
            replacement_value = 0
        
        replacement_values[position] = replacement_value
    
    return replacement_values

def calculate_injury_score(df):
    df['Injury_Score'] = 0

    # Debug: Check unique values in the Status and Practice columns
    print("Unique Status values:", df['Status'].unique())
    print("Unique Practice values:", df['Practice'].unique())

    # Apply penalty based on current injury status
    injury_status_penalty = {
        'Questionable': 1,
        'Doubtful': 2,
        'Out': 3
    }
    df['Injury_Score'] += df['Status'].map(injury_status_penalty).fillna(0)

    # Apply penalty based on practice status
    practice_penalty = {
        'Limited': 1,
        'Did Not Practice': 2
    }
    df['Injury_Score'] += df['Practice'].map(practice_penalty).fillna(0)

    # Normalize the injury score
    if df['Injury_Score'].max() > 0:
        df['Injury_Score'] = (df['Injury_Score'] - df['Injury_Score'].min()) / (df['Injury_Score'].max() - df['Injury_Score'].min())
    
    return df

def calculate_vorp(df, replacement_values):
    df['VORP'] = df.apply(lambda row: max(row['FantasyPointsPPR_proj'] - replacement_values.get(row['Position_stats'], 0), 0), axis=1)
    return df

def normalize(series):
    return (series - series.min()) / (series.max() - series.min())

def calculate_composite_score(df):
    df['Normalized_VORP'] = normalize(df['VORP'])
    df['Normalized_ADP'] = 1 - normalize(df['AverageDraftPositionPPR_proj'])  # Invert ADP so lower is better
    df['Normalized_Ceiling'] = normalize(df['FantasyPointsPPR_proj'])
    df['Normalized_Opportunity'] = normalize(df['ReceivingTargets_proj'] + df['RushingAttempts_proj'])

    # Calculate injury score
    df = calculate_injury_score(df)

    # Define weights adjusted for half-point PPR
    vorp_weight = 0.2
    adp_weight = 0.5
    ceiling_weight = 0.1
    opportunity_weight = 0.1
    injury_weight = 0.1

    # Calculate Composite Score
    df['Composite_Score'] = (
        (df['Normalized_VORP'] * vorp_weight) +
        (df['Normalized_ADP'] * adp_weight) +
        (df['Normalized_Ceiling'] * ceiling_weight) +
        (df['Normalized_Opportunity'] * opportunity_weight) +
        (df['Injury_Score'] * injury_weight)
    )

    return df

def process_data(raw_data_dir, processed_data_dir):
    raw_data_dir = os.path.abspath(raw_data_dir)
    processed_data_dir = os.path.abspath(processed_data_dir)

    player_projections_path = os.path.join(raw_data_dir, 'player_projections_2024.csv')
    player_stats_path = os.path.join(raw_data_dir, 'player_stats.csv')
    injury_reports_path = os.path.join(raw_data_dir, 'injury_reports.csv')

    if not os.path.isfile(player_stats_path):
        raise FileNotFoundError(f"Player stats file not found: {player_stats_path}")
    if not os.path.isfile(player_projections_path):
        raise FileNotFoundError(f"Player projections file not found: {player_projections_path}")
    if not os.path.isfile(injury_reports_path):
        raise FileNotFoundError(f"Injury reports file not found: {injury_reports_path}")

    player_stats = pd.read_csv(player_stats_path)
    player_projections = pd.read_csv(player_projections_path)
    injury_reports = pd.read_csv(injury_reports_path)

    merged_data = pd.merge(player_stats, player_projections, on='PlayerID', suffixes=('_stats', '_proj'))
    merged_data = pd.merge(merged_data, injury_reports, on='PlayerID', how='left')

    league_settings = {
        'QB': 1,
        'RB': 2,
        'WR': 2,
        'TE': 1,
        'FLEX': 1,
        'DST': 1,
        'K': 1,
        'num_teams': 12
    }

    replacement_values = calculate_replacement_values(merged_data, league_settings)
    merged_data = calculate_vorp(merged_data, replacement_values)
    merged_data = calculate_injury_score(merged_data)
    merged_data = calculate_composite_score(merged_data)

    os.makedirs(processed_data_dir, exist_ok=True)
    merged_data_path = os.path.join(processed_data_dir, 'merged_data_v2.csv')
    merged_data.to_csv(merged_data_path, index=False)
    print(f"Merged data saved to {merged_data_path}")

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw_data_dir = os.path.join(base_dir, 'data', 'raw')
    processed_data_dir = os.path.join(base_dir, 'data', 'processed')
    process_data(raw_data_dir, processed_data_dir)
