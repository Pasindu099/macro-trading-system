# scripts/utils/mappings.py

COUNTRY_FOLDER_MAP = {
    "US Data": "United States",
    "UK Data": "United Kingdom",
    "Canada Data": "Canada",
    "Australia Data": "Australia",
    "Swiss Data": "Switzerland",
}

CATEGORY_FOLDER_MAP = {
    "Economic Activity": "Economic Activity",
    "Inflation": "Inflation",
    "Inflation data": "Inflation",
    "Inflation Data": "Inflation",
    "Employment": "Employment",
    "Employment Data": "Employment",
    "Monetary policy": "Monetary Policy",
    "Monetary Policy": "Monetary Policy",
    "Monetory Policy": "Monetary Policy",
}

COLUMN_ALIASES = {
    "period_date": [
        "date",
        "period",
        "reference date",
        "observation date",
        "month",
        "quarter",
    ],
    "release_date": [
        "release date",
        "released",
        "publication date",
        "release",
    ],
    "actual_value": [
        "actual",
        "actual value",
        "value",
    ],
    "forecast_value": [
        "forecast",
        "forecast value",
        "consensus",
    ],
    "previous_value": [
        "previous",
        "prior",
        "previous value",
    ],
    "revised_value": [
        "revised",
        "revised value",
    ],
}