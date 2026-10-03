from datetime import date

import pytest

from app.ingestion.eodhd_client import GBOND_COUNTRY_MATURITIES
from app.services.curve_metrics import calculate_curve
from app.services.fx_spot import FX_PAIR_SYMBOLS
from app.services.fx_synthetic import synthesize_cross


def test_config_contains_france_long_end_and_all_28_fx_pairs():
    assert GBOND_COUNTRY_MATURITIES["FR"] == ("2Y", "10Y")
    for country in ("US", "DE", "UK", "JP", "AU", "CA"):
        assert "30Y" in GBOND_COUNTRY_MATURITIES[country]
    for country in ("FR", "NZ", "SW"):
        assert "30Y" not in GBOND_COUNTRY_MATURITIES[country]
    assert len(FX_PAIR_SYMBOLS) == 28


def test_synthetic_cross_multiplies_usd_legs_on_common_dates():
    day = date(2026, 8, 3)
    other = date(2026, 8, 4)
    legs = {"AUD/USD": {day: 0.7, other: 0.71}, "USD/CAD": {day: 1.4}}
    assert synthesize_cross("AUD/CAD", legs) == {day: pytest.approx(0.98)}
    assert synthesize_cross("EUR/GBP", {"EUR/USD": {day: 1.1}, "GBP/USD": {day: 1.25}}) == {
        day: pytest.approx(0.88)
    }


def test_30y_unavailable_becomes_available_when_data_arrives():
    day = date(2026, 8, 3)
    curves = {"2Y": {day: 2.0}, "10Y": {day: 3.0}}
    assert calculate_curve("US", curves)["ten_thirty"]["status"] == "unavailable"
    curves["30Y"] = {day: 3.5}
    result = calculate_curve("US", curves)
    assert result["ten_thirty"]["status"] == "available"
    assert result["ten_thirty"]["value_bp"] == pytest.approx(50.0)
