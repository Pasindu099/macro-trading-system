from decimal import Decimal

from app.services.news_assets import FRED_ASSETS, FX_ASSETS, METALS, valid_close


def test_asset_inventory_has_required_fourteen_series():
    assert len(set(FRED_ASSETS.values()) | set(FX_ASSETS.values()) | set(METALS.values())) == 14
    assert {"US_REAL_10Y", "US_10Y_BREAKEVEN", "DXY", "XAUUSD", "XAGUSD"} <= (
        set(FRED_ASSETS.values()) | set(FX_ASSETS.values()) | set(METALS.values())
    )


def test_metal_close_rejects_bad_and_nonpositive_values():
    assert valid_close({"close": "2371.25"}) == Decimal("2371.25")
    for bad in ({}, {"close": None}, {"close": "NaN"}, {"close": "-1"}, {"close": "0"}):
        assert valid_close(bad) is None
