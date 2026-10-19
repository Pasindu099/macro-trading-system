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


def test_sp500_comes_from_eodhd_with_fred_fallback():
    from app.services.news_assets import EODHD_EOD

    assert EODHD_EOD["GSPC.INDX"] == "SP500" and FRED_ASSETS["SP500"] == "SP500"


async def test_eodhd_failure_for_one_symbol_keeps_others(monkeypatch):
    import contextlib
    from datetime import date

    import httpx

    from app.services import news_assets

    written = []

    class Session:
        async def execute(self, statement, params=None):
            if isinstance(params, list):
                written.extend(params)

    @contextlib.asynccontextmanager
    async def scope():
        yield Session()

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def fetch_eod_history(self, symbol, *, from_date, to_date):
            if symbol == "GSPC.INDX":
                raise httpx.ConnectError("down")
            return [{"date": "2026-10-16", "close": "100.5"}]

    async def coverage():
        return {}

    monkeypatch.setattr(news_assets, "session_scope", scope)
    monkeypatch.setattr(news_assets, "EODHDClient", Client)
    monkeypatch.setattr(news_assets, "snapshot_coverage", coverage)
    await news_assets.refresh_snapshot(from_date=date(2026, 10, 10))
    assert {row["series"] for row in written} == {"XAUUSD", "XAGUSD"}  # SP500 keeps its FRED rows
