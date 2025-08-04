#update_data.py
import os
from data.data_processing import process_data
from data.data_aggregation import main as fetch_and_save_data

def update_data():
    print("Starting update_data()...")  # Debugging

    # Step 1: Fetch and save the latest raw data
    try:
        print("Calling fetch_and_save_data()...")  # Debugging
        fetch_and_save_data()
        print("Data fetching completed.")  # Debugging
    except Exception as e:
        print(f"Error during data fetching: {e}")  # Debugging
        return  # Exit the function if fetching fails

    # Step 2: Process the raw data and save it in processed format
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        raw_data_dir = os.path.join(base_dir, 'data', 'raw')
        processed_data_dir = os.path.join(base_dir, 'data', 'processed')

        print(f"Raw data directory: {raw_data_dir}")  # Debugging
        print(f"Processed data directory: {processed_data_dir}")  # Debugging

        print(f"Processing data from {raw_data_dir} to {processed_data_dir}...")  # Debugging
        process_data(raw_data_dir, processed_data_dir)
        print("Data processing completed.")  # Debugging
    except Exception as e:
        print(f"Error during data processing: {e}")  # Debugging
        return  # Exit the function if processing fails

if __name__ == "__main__":
    update_data()
