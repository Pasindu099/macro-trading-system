"""Dataset SQL, provenance policy, selection and CSV streams in PostgreSQL."""
import csv
import io
import json
from contextlib import asynccontextmanager
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.session import dispose_engine, get_sessionmaker
from app.services import data_exports, research_datasets as datasets


@pytest.fixture
async def session():
    async with get_sessionmaker()() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await dispose_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(datasets.DATASETS))
async def test_all_datasets_read_only(session, name):
    await session.execute(text("SET TRANSACTION READ ONLY"))
    result = await datasets.browse(session, name, limit=2)
    meta = result["dataset"]
    assert meta["description"] and meta["frequency"] and meta["sources"]
    assert meta["status"] in {"available", "empty"}
    assert meta["licence_flag"] in {"personal use", "redistributable"}
    assert len(result["rows"]) <= 2
    assert all(set(row) == set(datasets.COLUMNS) for row in result["rows"])
    if name == "model_rates" and result["rows"]:
        assert set(result["rows"][0]["details"]) >= {"inputs", "confidence", "missing_inputs", "status"}


@pytest.mark.asyncio
async def test_filters_dedup_coverage_and_export(session, monkeypatch):
    token = uuid4().hex
    for value, stamp, outlier in [(2, '1901-01-02', False), (3, '1901-01-03', False), (999, '1901-01-04', True)]:
        await session.execute(text("""
            INSERT INTO fx_spot_observations (provider, provider_symbol, pair, base_currency, quote_currency,
                close_value, observation_date, ingested_at, data_frequency, source_type, quality_status, is_outlier, payload_hash)
            VALUES ('test', :symbol, 'USD/SEK', 'USD', 'SEK', :value, '1901-01-02',
                CAST(CAST(:stamp AS text) AS timestamptz), 'daily', 'official', 'valid', :outlier, :hash)
        """), {"symbol": token, "value": value, "stamp": stamp, "outlier": outlier, "hash": token+stamp})
    filters = {"date_from": date(1901, 1, 1), "date_to": date(1901, 1, 3),
               "series": ["USD/SEK"], "currencies": ["SEK"]}
    result = await datasets.browse(session, "fx_spot", **filters)
    assert len(result["rows"]) == 1 and result["rows"][0]["value"] == 3
    assert result["dataset"]["coverage"] == {"first_date": date(1901, 1, 2), "last_date": date(1901, 1, 2), "rows": 1}
    empty = await datasets.browse(session, "fx_spot", **{**filters, "currencies": ["JPY"]})
    assert empty["rows"] == [] and empty["dataset"]["status"] == "empty"

    @asynccontextmanager
    async def connection():
        yield session

    monkeypatch.setattr(data_exports, "get_sessionmaker", lambda: connection)
    response = datasets.export_dataset("fx_spot", **filters)
    assert response.headers["x-export-row-limit"] == "50000"
    chunks = [chunk async for chunk in response.body_iterator]
    rows = list(csv.DictReader(io.StringIO("".join(chunks))))
    assert len(rows) == 1 and rows[0]["series"] == "USD/SEK"
    assert json.loads(rows[0]["currencies"]) == ["USD", "SEK"]
    assert json.loads(rows[0]["details"])["source_type"] == "official"


@pytest.mark.asyncio
async def test_dataset_csv_cap_is_enforced_in_sql_and_iterator(session, monkeypatch):
    @asynccontextmanager
    async def connection():
        yield session

    monkeypatch.setattr(data_exports, "get_sessionmaker", lambda: connection)
    monkeypatch.setattr(data_exports, "CSV_LIMIT", 2)
    # generate_series exercises a real database stream with more source rows than the cap.
    response = data_exports.csv_response(
        "SELECT n AS value FROM generate_series(1,10) n LIMIT :export_limit", {}, ["value"], "test.csv")
    rows = list(csv.DictReader(io.StringIO("".join([chunk async for chunk in response.body_iterator]))))
    assert [r["value"] for r in rows] == ["1", "2"]
    response = data_exports.csv_response("SELECT n AS value FROM generate_series(1,10) n", {}, ["value"], "test.csv")
    rows = list(csv.DictReader(io.StringIO("".join([chunk async for chunk in response.body_iterator]))))
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_public_demo_policy_excludes_list_reads_and_exports(session, monkeypatch):
    config = datasets.load_config()
    assert datasets.dataset_spec("fx_spot")["licence_flag"] == "personal use"
    config["show_unlicensed_sources"] = False
    monkeypatch.setattr(datasets, "load_config", lambda: config)
    visible = await datasets.catalogue(session)
    assert visible and all(row["licensed_for_redistribution"] for row in visible)
    assert "fx_spot" not in {row["name"] for row in visible}
    with pytest.raises(LookupError):
        await datasets.browse(session, "fx_spot")
    with pytest.raises(LookupError):
        datasets.export_dataset("fx_spot")


def test_expected_series_and_input_validation():
    fx = datasets.dataset_spec("fx_spot")
    cross = datasets.dataset_spec("cross_asset")
    assert len(fx["expected_series"]) == 30 and "USD/DXY" in fx["expected_series"] and "USD/SEK" in fx["expected_series"]
    assert len(cross["expected_series"]) == 14
    with pytest.raises(ValueError):
        datasets.query("fx_spot", date_from=date(2026, 2, 1), date_to=date(2026, 1, 1))
    with pytest.raises(ValueError):
        datasets.query("fx_spot", currencies=["INVALID"])
    with pytest.raises(LookupError):
        datasets.query("nonexistent")


@pytest.mark.asyncio
async def test_named_spread_currency_filter(session):
    await session.execute(text("""
        INSERT INTO yield_spreads (spread_name,tenor,obs_date,base_yield,quote_yield,spread_bp)
        VALUES ('FR-DE','10Y','1901-01-02',3,2,100)
    """))
    result = await datasets.browse(session, "yield_spreads", date_from=date(1901,1,1), date_to=date(1901,1,3), currencies=["EUR"])
    assert len(result["rows"]) == 1
    assert result["rows"][0]["series"] == "FR-DE:10Y" and result["rows"][0]["value"] == 100


@pytest.mark.asyncio
async def test_csv_formula_strings_are_safe(session, monkeypatch):
    @asynccontextmanager
    async def connection():
        yield session

    monkeypatch.setattr(data_exports, "get_sessionmaker", lambda: connection)
    response = data_exports.csv_response("SELECT '=DANGEROUS()' AS label, -2 AS value LIMIT :export_limit", {},
                                         ["label", "value"], "safe.csv")
    rows = list(csv.DictReader(io.StringIO("".join([chunk async for chunk in response.body_iterator]))))
    assert rows == [{"label": "'=DANGEROUS()", "value": "-2"}]
