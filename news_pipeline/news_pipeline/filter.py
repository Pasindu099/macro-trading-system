from __future__ import annotations

import re

MACRO_KEYWORDS: dict[str, list[str]] = {
    "central_bank": [
        "federal reserve", "fed", "fomc", "ecb", "boe", "boj", "rba", "rbnz", "boc", "snb",
        "rate decision", "interest rate", "rate hike", "rate cut", "rate pause",
        "quantitative easing", "quantitative tightening", "tapering", "balance sheet",
        "powell", "lagarde", "bailey", "ueda", "governor", "central bank governor",
        "monetary policy", "policy statement", "dot plot", "hawkish", "dovish",
        "basis points", "bps", "terminal rate"
    ],
    "inflation": [
        "cpi", "inflation", "core inflation", "pce", "core pce", "ppi",
        "producer prices", "consumer prices", "disinflation", "stagflation",
        "cost of living", "price pressures"
    ],
    "employment": [
        "nonfarm payroll", "nfp", "unemployment", "jobs report", "jobless claims",
        "labor market", "wage growth", "average hourly earnings", "participation rate",
        "job openings", "jolts", "layoffs", "hiring freeze"
    ],
    "growth": [
        "gdp", "gross domestic product", "recession", "economic growth",
        "manufacturing pmi", "services pmi", "ism", "retail sales",
        "consumer confidence", "consumer sentiment", "industrial production",
        "durable goods", "housing starts", "trade balance", "trade deficit"
    ],
    "geopolitical": [
        "war", "conflict", "sanctions", "invasion", "ceasefire", "strike",
        "military", "attack", "missile", "airstrike", "coup", "border clash",
        "nuclear", "troop", "escalation", "peace talks", "embargo"
    ],
    "politics": [
        "election", "president", "prime minister", "parliament", "congress",
        "senate", "government shutdown", "debt ceiling", "impeachment",
        "resignation", "no confidence vote", "coalition", "referendum",
        "trade deal", "tariff", "trade war", "diplomatic", "summit"
    ],
    "energy": [
        "crude oil", "oil price", "wti", "brent", "opec", "opec+",
        "oil production", "oil supply", "pipeline", "refinery",
        "energy crisis", "natural gas", "strategic petroleum reserve", "spr"
    ],
    "gold_specific": [
        "gold", "safe haven", "bullion", "xau", "precious metals",
        "gold reserves", "central bank gold buying", "gold etf"
    ],
    "risk_sentiment": [
        "sell-off", "selloff", "risk-off", "risk-on", "flight to safety",
        "market volatility", "vix", "stock market crash", "bond yields",
        "yield curve", "credit spreads", "liquidity crisis"
    ],
}

NEGATIVE_KEYWORDS: list[str] = [
    "gold medal", "gold cup", "gold record", "war on drugs", "war of words",
    "war on poverty", "president of the company", "president of the club",
    "prime minister's cup", "trade deal for", "governor of the board",
    "strike out", "strike rate", "military academy sports"
]

HARD_MACRO_OVERRIDE: list[str] = [
    "cpi", "nonfarm payroll", "nfp", "rate decision", "fomc", "core pce",
    "gdp", "jobless claims"
]


def passes_filter(title: str, body: str) -> tuple[bool, list[str]]:
    text = _normalize_text(title, body)
    matched_categories = [
        category
        for category, keywords in MACRO_KEYWORDS.items()
        if any(_term_matches(text, keyword) for keyword in keywords)
    ]

    if not matched_categories:
        return False, []

    has_negative_match = any(_term_matches(text, keyword) for keyword in NEGATIVE_KEYWORDS)
    if has_negative_match and not _has_hard_macro_override(text):
        return False, matched_categories

    return True, matched_categories


def _normalize_text(title: str, body: str) -> str:
    return f"{title or ''} {body or ''}".lower()


def _has_hard_macro_override(text: str) -> bool:
    return any(_term_matches(text, term) for term in HARD_MACRO_OVERRIDE)


def _term_matches(text: str, term: str) -> bool:
    normalized_term = term.strip().lower()
    if not normalized_term:
        return False

    escaped = re.escape(normalized_term).replace(r"\ ", r"\s+")
    prefix = r"(?<![a-z0-9])" if normalized_term[0].isalnum() else ""
    suffix = r"(?![a-z0-9])" if normalized_term[-1].isalnum() else ""
    return re.search(f"{prefix}{escaped}{suffix}", text) is not None
