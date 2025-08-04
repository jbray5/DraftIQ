#main.py
import sys
import os
from update_data import update_data
from gui_player_display import PlayerDisplayApp
from PyQt5.QtWidgets import QApplication

def main():
    # Path to the processed data file
    base_dir = os.path.dirname(os.path.abspath(__file__))
    processed_data_dir = os.path.join(base_dir, 'processed')
    ranked_players_path = os.path.join(processed_data_dir, 'merged_data_v2.csv')
    
    # Delete the existing processed data file if it exists
    if os.path.exists(ranked_players_path):
        print(f"Deleting existing file: {ranked_players_path}")
        os.remove(ranked_players_path)

    # Step 1: Pull fresh data from the API, clean it, and merge it
    print("Updating data by calling the data aggregation script...")
    
    try:
        update_data()
        print("Data update completed.")
    except Exception as e:
        print(f"Error during data update: {e}")
        return
    
    # Check if the processed data file exists
    print(f"Checking for processed data file at: {ranked_players_path}")
    if not os.path.isfile(ranked_players_path):
        print(f"File {ranked_players_path} does not exist.")
        raise FileNotFoundError(f"The processed data file was not found: {ranked_players_path}")
    else:
        print(f"File found: {ranked_players_path}")

    # Step 3: Run the GUI application
    print("Starting the GUI application...")
    app = QApplication(sys.argv)
    window = PlayerDisplayApp(ranked_players_path)
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
