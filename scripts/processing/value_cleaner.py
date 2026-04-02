import re
import pandas as pd


def clean_numeric_value(value):
    """
    Converts messy numeric values into clean floats.

    Handles:
    - percentages (0.003 → 0.3)
    - K values (10K → 10000)
    - strings like '1.2%' or '10K'
    """

    if pd.isna(value):
        return None

    # Convert to string
    value_str = str(value).strip().lower()

    try:
        # Handle K values (e.g., 10K → 10000)
        if "k" in value_str:
            number = float(value_str.replace("k", ""))
            return number * 1000

        # Handle % values like '0.3%'
        if "%" in value_str:
            number = float(value_str.replace("%", ""))
            return number

        # Handle small decimals like 0.003 → 0.3
        number = float(value_str)

        # Heuristic: if value < 1, assume it's percentage format
        if abs(number) < 1:
            return number * 100

        return number

    except:
        return None
