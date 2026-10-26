"""Whiteboard-only translation of indicator primary categories."""

CATEGORY_ORDER = ("central_bank", "rates", "labor", "inflation", "growth", "flow", "other")

PRIMARY_CATEGORY = {
    "Monetary Policy": "central_bank",
    "Labor": "labor",
    "Inflation": "inflation",
    "Growth": "growth",
    "Housing": "growth",
    "Sentiment": "growth",
    "Trade": "flow",
}


def library_category(primary_category: str) -> str:
    return PRIMARY_CATEGORY.get(primary_category, "other")
