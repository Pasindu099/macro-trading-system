from itertools import combinations
from pathlib import Path

import yaml


def test_all_market_convention_pairs_and_pip_sizes():
    data = yaml.safe_load(Path("config/pairs.yaml").read_text(encoding="utf-8"))
    priority = data["currency_priority"]
    pairs = data["pairs"]
    assert len(pairs) == 28
    assert [row["pair"] for row in pairs] == [f"{base}/{quote}" for base, quote in combinations(priority, 2)]
    for row in pairs:
        assert row["pair"] == f"{row['base']}/{row['quote']}"
        assert row["pip_size"] == (0.01 if row["quote"] == "JPY" else 0.0001)


def test_yield_benchmarks_and_missing_long_tenors():
    data = yaml.safe_load(Path("config/pairs.yaml").read_text(encoding="utf-8"))
    assert data["currency_benchmarks"]["EUR"] == "DE"
    assert data["regime_tenors"] == ["2Y", "10Y"]
    assert set(data["tenor_fallbacks"]) == {"US", "DE", "FR", "UK", "JP", "AU", "NZ", "CA", "CH"}
    assert all(tenors["30Y"] is None for tenors in data["tenor_fallbacks"].values())
    assert data["named_spreads"] == [{
        "name": "FR-DE", "base_country": "FR", "quote_country": "DE",
        "tenors": ["2Y", "10Y", "30Y"],
    }]
