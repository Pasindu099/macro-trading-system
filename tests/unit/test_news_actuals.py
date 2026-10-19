from decimal import Decimal
from datetime import datetime, UTC
from types import SimpleNamespace

import pytest

from app.services.news_actuals import choose_candidate, parse_headline
from app.ingestion.canonicalizer import Canonicalizer
from app.ingestion.ingest_service import IngestService
from app.db.models import IndicatorRelease


def test_parse_explicit_cpi_print():
    result = parse_headline("US September CPI +0.4% vs +0.3% expected")
    assert result is not None
    assert (result.country, result.canonical_prefix, result.actual, result.consensus) == (
        "US", "cpi_headline", Decimal("0.4"), Decimal("0.3"))


def test_parse_lower_is_better_unemployment_print():
    result = parse_headline("UK unemployment rate 4.1% vs 4.3% expected")
    assert result is not None
    assert (result.country, result.canonical_prefix, result.actual) == (
        "UK", "unemployment_rate", Decimal("4.1"))


def test_previews_and_ambiguous_headlines_are_not_parsed():
    assert parse_headline("US September CPI preview: 0.4% expected") is None
    assert parse_headline("CPI 0.4% vs 0.3% expected") is None


def test_forecast_resolves_co_releases_but_not_different_indicators_with_same_forecast():
    parsed = parse_headline("US September CPI 3.7% vs 3.6% expected")
    rows = [{"id": 1, "canonical_name": "cpi_headline_mom", "period": "Sep", "released_at": 1,
             "estimate": Decimal("0.6")},
            {"id": 2, "canonical_name": "cpi_headline_yoy", "period": "Sep", "released_at": 1,
             "estimate": Decimal("3.6")}]
    assert choose_candidate(rows, parsed) == 2
    rows.append({**rows[1], "id": 3})
    assert choose_candidate(rows, parsed) == 3
    rows.append({**rows[1], "id": 4, "canonical_name": "cpi_other_yoy"})
    assert choose_candidate(rows, parsed) is None



@pytest.mark.parametrize("title,country,name,actual,consensus", [
    ("US September non-farm payrolls +29K vs +90K expected", "US", "nfp", "29", "90"),
    ("US initial jobless claims 197K vs 201K expected", "US", "initial_jobless_claims", "197", "201"),
    ("ISM non- manufacturing PMI 54.9 versus 55.2 estimate", "US", "ism_services_pmi", "54.9", "55.2"),
    ("Australian September 2026 unemployment rate 4.7% (expected 4.6%, prior 4.7%)", "AU", "unemployment_rate",
     "4.7", "4.6"),
    ("China private survey manufacturing PMI (September) 52.1 (vs. expected 51.6, prior 51.5)", "CN",
     "manufacturing_pmi", "52.1", "51.6"),
    ("US existing home sales for September 3.99 million versus 3.95 million estimate", "US", "existing_home_sales",
     "3.99", "3.95"),
    ("Tokyo core CPI jumps to 2.7%, fastest in 10 months", "JP", "tokyo_core_cpi", "2.7", None),
    ("Bank of Canada Q3 business outlook survey -0.33 vs -0.38 prior", "CA", "boc_business_outlook_survey",
     "-0.33", None),
])
def test_parse_extended_formats(title, country, name, actual, consensus):
    result = parse_headline(title)
    assert result is not None and result.country == country and result.names[0] == name
    assert result.actual == Decimal(actual)
    assert result.consensus == (Decimal(consensus) if consensus else None)


def test_euro_area_code_flash_names_and_prelim_reference():
    final = parse_headline("Eurozone September final CPI +3.8% vs +3.8% y/y prelim")
    assert final.country == "EU" and final.consensus is None and final.reference == Decimal("3.8")
    assert final.names == ("cpi_headline_yoy", "consumer_price_index_yoy", "monthly_cpi_indicator_yoy", "cpi_monthly_yoy")
    flash = parse_headline("US September S&P Global flash services PMI 58.7 vs 56.0 expected")
    assert "sp_global_services_pmi" in flash.names and "services_pmi_flash" in flash.names
    durable = parse_headline("US August durable goods orders 0.0% vs -0.4% expected")
    assert not any(n.startswith("durable_goods_ex") for n in durable.names)  # never the ex-transport series


@pytest.mark.parametrize("title", [
    "PBOC sets USD/ CNY reference rate for today at 6.7208 (vs. estimate at 6.6897)",
    "Atlanta Fed GDPNow Q3 tracker 3.6% vs 3.7% prior",
    "Japanese firms expect consumer prices to rise 2.6% a year from now, down from 2.7%",
    "MUFG: US CPI seen at 3.6%, core must surprise to revive an October Fed hike",
    "China factory inflation hits 5%, fastest in four years, as CPI lags at 0.9%",
])
def test_non_releases_and_mixed_clauses(title):
    result = parse_headline(title)
    if "factory inflation" in title:
        assert result.names[0] == "ppi" and result.actual == Decimal("5")  # first clause only, not CPI 0.9
    else:
        assert result is None

@pytest.mark.asyncio
async def test_eodhd_confirmation_versions_newswire_even_when_actual_agrees():
    latest = IndicatorRelease(id=9, indicator_id=2, actual=Decimal("3.7"),
                              estimate=Decimal("3.6"), previous=Decimal("3.4"),
                              raw_payload={"source_status": "newswire"}, is_latest=True)
    canonical = SimpleNamespace(period_start_date=None, period_raw="Sep",
                                released_at=datetime(2026, 10, 14, 12, 30, tzinfo=UTC),
                                actual=Decimal("3.7"), estimate=Decimal("3.6"),
                                previous=Decimal("3.4"), change=None, change_percentage=None,
                                raw_payload={"source": "eodhd"})

    class Session:
        def __init__(self): self.queries = []; self.added = []
        async def execute(self, statement, params=None):
            self.queries.append(str(statement))
            return SimpleNamespace(scalar_one_or_none=lambda: latest)
        async def flush(self): pass
        def add(self, row): self.added.append(row)

    session = Session()
    service = IngestService(Canonicalizer([]))
    assert await service._upsert_release(session, SimpleNamespace(id=2), canonical) == "updated"
    assert len(session.added) == 1
    assert "UPDATE newswire_actuals" in session.queries[-1]
    session = Session()
    canonical.actual = None
    assert await service._upsert_release(session, SimpleNamespace(id=2), canonical) == "same"
    assert not session.added
