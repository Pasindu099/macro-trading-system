import pandas as pd
from scripts.utils.db_connection import get_engine

file_path = r"C:\Users\wpmpo\OneDrive\Documents\macro_trading_system\data\raw\US Data\Inflation data\U.S. Consumer Price Index (CPI) MoM.xlsx"

# Read Excel
df = pd.read_excel(file_path)

# Keep relevant columns
df = df[['Release date', 'Actual', 'Forecast', 'Previous']]

# Rename columns
df.columns = ['date_raw', 'actual', 'forecast', 'previous']

# Clean date
df['date'] = df['date_raw'].str.extract(r'^(.*?\d{4})')
df['date'] = pd.to_datetime(df['date'])

# Drop raw column
df = df.drop(columns=['date_raw'])

# Add metadata
df['country'] = 'US'
df['category'] = 'Inflation'
df['indicator'] = 'CPI MoM'

# Reorder columns to match DB
df = df[['date', 'country', 'category', 'indicator', 'actual', 'forecast', 'previous']]

# Sort
df = df.sort_values(by='date')

# 🔥 CONNECT TO DATABASE
engine = get_engine()

# 🔥 INSERT INTO DATABASE
df.to_sql(
    'macro_data',
    engine,
    if_exists='append',
    index=False
)

print("✅ Data inserted into PostgreSQL!")
print(df.head())