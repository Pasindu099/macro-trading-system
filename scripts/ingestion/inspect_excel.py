from pathlib import Path
import pandas as pd

from scripts.utils.mappings import COUNTRY_FOLDER_MAP, CATEGORY_FOLDER_MAP


def extract_metadata(file_path: Path) -> dict:
    """
    Extract country, category, and raw indicator name from file path.
    Expected structure example:
    data/raw/US Data/Inflation data/U.S. CPI YoY.xlsx
    """
    parts = file_path.parts

    country_folder = None
    category_folder = None

    for part in parts:
        if part in COUNTRY_FOLDER_MAP:
            country_folder = part
        if part in CATEGORY_FOLDER_MAP:
            category_folder = part

    return {
        "country_folder": country_folder,
        "country_name": COUNTRY_FOLDER_MAP.get(country_folder),
        "category_folder": category_folder,
        "category_name": CATEGORY_FOLDER_MAP.get(category_folder),
        "raw_indicator_name": file_path.stem,
        "file_name": file_path.name,
        "full_path": str(file_path),
    }


def inspect_excel_file(file_path: Path) -> None:
    metadata = extract_metadata(file_path)

    print("\n=== FILE METADATA ===")
    for key, value in metadata.items():
        print(f"{key}: {value}")

    try:
        excel_file = pd.ExcelFile(file_path)
        print("\n=== SHEET NAMES ===")
        print(excel_file.sheet_names)

        df = pd.read_excel(file_path, sheet_name=0)

        print("\n=== DATA PREVIEW ===")
        print(df.head())

        print("\n=== COLUMNS ===")
        print(df.columns.tolist())

        print("\n=== SHAPE ===")
        print(df.shape)

    except Exception as e:
        print("\nFailed to inspect Excel file.")
        print("Error:", e)


if __name__ == "__main__":
    file_path = Path(r"C:\Users\wpmpo\OneDrive\Documents\macro_trading_system\data\raw\US Data\Inflation data\U.S. Consumer Price Index (CPI) MoM.xlsx")
    inspect_excel_file(file_path)