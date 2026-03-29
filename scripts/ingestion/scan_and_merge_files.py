import pandas as pd
import os

# Base folder
BASE_PATH = "data/raw"

all_data = []

for root, dirs, files in os.walk(BASE_PATH):
    for file in files:
        if file.endswith(".xlsx"):
            
            file_path = os.path.join(root, file)
            
            try:
                df = pd.read_excel(file_path)
                
                # Skip empty files
                if df.empty:
                    continue
                
                # Extract metadata from path
                parts = root.split(os.sep)
                
                country_raw = parts[-2]
                category_raw = parts[-1]
                
                indicator_raw = file.replace(".xlsx", "")
                
                # Add metadata columns
                df["country_raw"] = country_raw
                df["category_raw"] = category_raw
                df["indicator_raw"] = indicator_raw
                df["source_file"] = file
                
                all_data.append(df)
            
            except Exception as e:
                print(f"Error reading {file_path}: {e}")

# Merge all
master_df = pd.concat(all_data, ignore_index=True)

# Save
os.makedirs("data/staging", exist_ok=True)
master_df.to_csv("data/staging/merged_raw_releases.csv", index=False)

print(" All files merged successfully!")