import pandas as pd
import queue
import threading
import json
import time

CSV_FILE = "machine_temperature_system_failure.csv"
OUTPUT_JSON = "features.jsonl"
OUTPUT_PARQUET = "features.parquet"
WINDOW_SIZE = 12 # 1 hour if 5-minute granularity

def producer(q, filepath):
    print("Producer started.")
    try:
        # Simulate streaming by reading CSV file line by line
        with open(filepath, 'r') as f:
            header = f.readline()
            for line in f:
                if not line.strip():
                    continue
                timestamp_str, value_str = line.strip().split(',')
                try:
                    value = float(value_str)
                except ValueError:
                    continue
                
                # Emit row to queue
                q.put({'timestamp': timestamp_str, 'value': value})
                
    except FileNotFoundError:
        print(f"Error: {filepath} not found.")
    
    # Send a sentinel value to indicate end of stream
    q.put(None)
    print("Producer finished.")

def consumer(q, output_json, output_parquet):
    print("Consumer started.")
    
    window = []
    features_list = []
    
    while True:
        data = q.get()
        if data is None:
            break
        
        timestamp = data['timestamp']
        value = data['value']
        
        # Add to rolling window
        window.append(value)
        if len(window) > WINDOW_SIZE:
            window.pop(0)
            
        # Extract features (rolling mean, rolling std, rate of change)
        if len(window) > 0:
            rolling_mean = sum(window) / len(window)
            variance = sum((x - rolling_mean) ** 2 for x in window) / len(window)
            rolling_std = variance ** 0.5
            rate_of_change = value - window[-2] if len(window) >= 2 else 0.0
        else:
            rolling_mean = value
            rolling_std = 0.0
            rate_of_change = 0.0
            
        feature_record = {
            'timestamp': timestamp,
            'value': value,
            'rolling_mean': rolling_mean,
            'rolling_std': rolling_std,
            'rate_of_change': rate_of_change
        }
        
        features_list.append(feature_record)
        
    # Write output to jsonl
    print(f"Writing {len(features_list)} records to {output_json}")
    with open(output_json, 'w') as f:
        for record in features_list:
            f.write(json.dumps(record) + '\n')
            
    # Write to parquet
    try:
        df = pd.DataFrame(features_list)
        df.to_parquet(output_parquet, index=False)
        print(f"Successfully wrote features to {output_parquet}")
    except ImportError as e:
        print(f"Could not write to parquet due to missing dependencies (e.g. pyarrow, fastparquet): {e}. JSONL features are available.")
    except Exception as e:
        print(f"Error writing to parquet: {e}")
        
    print("Consumer finished.")

def main():
    q = queue.Queue(maxsize=5000)
    
    prod_thread = threading.Thread(target=producer, args=(q, CSV_FILE))
    cons_thread = threading.Thread(target=consumer, args=(q, OUTPUT_JSON, OUTPUT_PARQUET))
    
    prod_thread.start()
    cons_thread.start()
    
    prod_thread.join()
    cons_thread.join()
    print("Pipeline execution completed.")

if __name__ == "__main__":
    main()
