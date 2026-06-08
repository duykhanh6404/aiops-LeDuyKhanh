import sys
import os
import argparse
import pandas as pd
from collections import Counter
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
import re
from datetime import datetime, timedelta

def extract_timestamp(line):
    # Try HDFS format: 081109 203615
    match_hdfs = re.search(r'^(\d{6})\s(\d{6})', line)
    if match_hdfs:
        try:
            return datetime.strptime(f"{match_hdfs.group(1)} {match_hdfs.group(2)}", "%y%m%d %H%M%S")
        except:
            pass
    
    # Try ISO-like or standard logs: 2024-01-15 10:23:45 or 2024/01/15 10:23:45
    match_iso = re.search(r'(\d{4}[-/]\d{2}[-/]\d{2}\s\d{2}:\d{2}:\d{2})', line)
    if match_iso:
        try:
            dt_str = match_iso.group(1).replace('/', '-')
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except:
            pass
            
    # Try BGL format (timestamp is the first field, epoch seconds, e.g. 1117838570)
    # Actually BGL is usually: "-" "-" "-" "-" "1117838570" "2005.06.03" ...
    # Or spark: 17/06/09 20:10:40 ...
    match_spark = re.search(r'^(\d{2}/\d{2}/\d{2}\s\d{2}:\d{2}:\d{2})', line)
    if match_spark:
        try:
            return datetime.strptime(match_spark.group(1), "%y/%m/%d %H:%M:%S")
        except:
            pass
            
    return None

def analyze_log(log_file):
    if not os.path.exists(log_file):
        print(f"File not found: {log_file}")
        return
        
    print(f"Analyzing log file: {log_file}")
    
    # 1. Parse lines and get timestamps
    lines = []
    with open(log_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)
                
    total_lines = len(lines)
    if total_lines == 0:
        print("Empty log file.")
        return
        
    config = TemplateMinerConfig()
    config.drain_sim_th = 0.5
    miner = TemplateMiner(config=config)
    
    parsed_logs = []
    
    for line in lines:
        res = miner.add_log_message(line)
        parsed_logs.append({
            'log': line,
            'template': res['template_mined'],
            'cluster_id': res['cluster_id'],
            'timestamp': extract_timestamp(line)
        })
        
    clusters = miner.drain.clusters
    total_unique_templates = len(clusters)
    
    print("\n--- 1. Basic Stats ---")
    print(f"Total lines: {total_lines}")
    print(f"Total unique templates: {total_unique_templates}")
    
    # 2. Top 5 templates
    print("\n--- 2. Top-5 Templates ---")
    cluster_counts = [(c.cluster_id, c.get_template(), c.size) for c in clusters]
    cluster_counts.sort(key=lambda x: x[2], reverse=True)
    
    for idx, (cid, template, count) in enumerate(cluster_counts[:5]):
        pct = (count / total_lines) * 100
        print(f"Top {idx+1} (Count: {count}, {pct:.2f}%): {template}")
        
    # Process Timestamps
    df = pd.DataFrame(parsed_logs)
    
    valid_ts = df['timestamp'].dropna()
    if valid_ts.empty:
        print("\nCould not extract timestamps from the logs. Skipping time-based analysis.")
        return
        
    max_time = valid_ts.max()
    one_hour_ago = max_time - timedelta(hours=1)
    
    df_past = df[df['timestamp'] < one_hour_ago]
    df_recent = df[df['timestamp'] >= one_hour_ago]
    
    past_lines = len(df_past)
    recent_lines = len(df_recent)
    
    if past_lines == 0 or recent_lines == 0:
        print("\nNot enough time span to compare 'last hour' vs 'before last hour'.")
        return
        
    # 3. Sudden spikes in the last hour
    print("\n--- 3. Template Spikes (Last 1 hour) ---")
    # Compare hourly rate
    time_span_past_hours = (one_hour_ago - valid_ts.min()).total_seconds() / 3600.0
    if time_span_past_hours <= 0:
        time_span_past_hours = 1.0 # fallback
        
    past_counts = df_past['template'].value_counts()
    recent_counts = df_recent['template'].value_counts()
    
    spikes = []
    for template, r_count in recent_counts.items():
        p_count = past_counts.get(template, 0)
        p_rate = p_count / time_span_past_hours # occurrences per hour in the past
        r_rate = r_count # occurrences per hour in the recent 1 hr
        
        # Define spike: at least 5 occurrences, and rate > 2x past rate
        if r_rate >= 5 and r_rate > (p_rate * 2):
            spikes.append((template, p_rate, r_rate))
            
    spikes.sort(key=lambda x: x[2] / (x[1] + 1e-9), reverse=True)
    
    if not spikes:
        print("No sudden spikes detected in the last hour.")
    else:
        for idx, (template, p_rate, r_rate) in enumerate(spikes[:5]):
            print(f"Spike {idx+1}: {template}")
            print(f"  -> Past avg: {p_rate:.2f}/hr | Last hour: {r_rate:.2f}/hr")
            
    # 4. New templates in the last hour
    print("\n--- 4. New Templates (Appeared ONLY in the last 1 hour) ---")
    past_templates = set(df_past['template'].unique())
    recent_templates = set(df_recent['template'].unique())
    
    new_templates = recent_templates - past_templates
    if not new_templates:
        print("No new templates detected in the last hour.")
    else:
        for i, template in enumerate(list(new_templates)[:5]):
            print(f"New Template {i+1}: {template}")
            
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mini Log Analyzer")
    parser.add_argument("logfile", help="Path to the log file")
    args = parser.parse_args()
    
    analyze_log(args.logfile)
