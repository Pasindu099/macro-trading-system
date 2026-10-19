from datetime import date, datetime, timezone

import pytest

from app.services.calendar_service import _shape, impact_for, list_events
from app.processing.cb_preferred_score import MANDATE_WEIGHTS


def row(**changes):
    base = dict(id=1, indicator_id=2, released_at=datetime(2024, 1, 2, 13, tzinfo=timezone.utc),
                period="Dec", actual=3, estimate=2, previous=1, first_previous=0,
                raw_payload={}, retrieved_at=None, display_name="CPI", canonical_name="cpi",
                importance=3, unit="%", country_code="US", currency_code="USD",
                country_name="United States", surprise_normalized=1.5,
                is_higher_better_for_currency=True,
                bundle_id=None, bundle_key=None)
    return base | changes


def test_overrides_and_bundle_fallback():
    assert impact_for("Caixin Manufacturing PMI", 3) == 1
    assert impact_for("Minor auction", 3) == 3
    events = _shape([row(canonical_name="cpi_headline_mom", display_name="Headline CPI (MoM)"),
                     row(id=2, canonical_name="core_cpi_mom", display_name="Core CPI (MoM)",
                         surprise_normalized=-2)],
                    None, 1, False)
    assert len(events) == 1
    assert events[0]["name"] == "Inflation Rate"
    assert len(events[0]["components"]) == 2
    assert events[0]["surprise_sigma"] == -2
    assert events[0]["components"][0]["canonical_name"] == "core_cpi_mom"
    assert events[0]["components"][0]["revised_from"] == 0


def test_lower_unemployment_is_a_beat_and_higher_is_a_miss():
    lower = _shape([row(display_name="Unemployment Rate", actual=3.8, estimate=4.0,
                        surprise_normalized=1.2, is_higher_better_for_currency=False)], None, 3, False)
    higher = _shape([row(display_name="Unemployment Rate", actual=4.2, estimate=4.0,
                         surprise_normalized=-1.2, is_higher_better_for_currency=False)], None, 3, False)
    assert lower[0]["components"][0]["result"] == "beat"
    assert higher[0]["components"][0]["result"] == "miss"


def test_coincident_unrelated_events_do_not_merge_and_history_headline_is_mapped():
    events = _shape([
        row(id=10, indicator_id=None, canonical_name=None, display_name=None,
            raw_payload={"type": "Inflation Rate", "country": "US"}, actual=None),
        row(id=11, canonical_name="core_cpi_mom", display_name="Core CPI (MoM)",
            actual=0.3, estimate=0.2),
        row(id=12, canonical_name="cpi_headline_yoy", display_name="Headline CPI (YoY)",
            actual=3.0, estimate=2.9),
        row(id=13, canonical_name="real_earnings", display_name="Real Earnings", importance=3),
    ], None, 1, False)
    assert len(events) == 1
    cpi = next(event for event in events if event["name"] == "Inflation Rate")
    assert [member["release_id"] for member in cpi["components"]] == [11, 12]
    assert cpi["components"][0]["indicator_id"] == 2
    assert cpi["actual"] == 0.3
    all_events = _shape([
        row(id=10, indicator_id=None, canonical_name=None, display_name=None,
            raw_payload={"type": "Inflation Rate", "country": "US"}),
        row(id=11, canonical_name="core_cpi_mom", display_name="Core CPI (MoM)"),
        row(id=13, canonical_name="real_earnings", display_name="Real Earnings"),
    ], None, 3, False)
    assert len(all_events) == 3


def test_cpi_components_use_standard_source_names():
    source = [
        ("core_cpi_mom", "Core Inflation Rate", "mom"),
        ("core_cpi_yoy", "Core Inflation Rate", "yoy"),
        ("cpi_headline_mom", "Inflation Rate", "mom"),
        ("cpi_headline_yoy", "Inflation Rate", "yoy"),
    ]
    events = _shape([row(id=i, canonical_name=canonical,
                         raw_payload={"type": name, "comparison": comparison})
                     for i, (canonical, name, comparison) in enumerate(source, 1)], None, 3, False)
    assert len(events) == 1
    assert [component["name"] for component in events[0]["components"]] == [
        "Core Inflation Rate MoM", "Core Inflation Rate YoY",
        "Inflation Rate MoM", "Inflation Rate YoY",
    ]


def test_china_calendar_only_and_unmapped():
    assert "CN" not in MANDATE_WEIGHTS
    events = _shape([row(country_code=None, currency_code="CNY", raw_payload={"country": "CN", "type": "PMI"},
                         indicator_id=None, display_name=None, surprise_normalized=None)], None, 3, False)
    assert events[0]["country_code"] == "CN"
    assert events[0]["components"][0]["mapped"] is False


def test_bundle_headline_is_top_weighted_member_not_largest_surprise():
    bundle = dict(bundle_id=7, bundle_key="US_NFP_DAY", importance=1)
    events = _shape([row(id=1, canonical_name="participation_rate", display_name="Participation Rate",
                         actual=61.8, surprise_normalized=2.5, **bundle),
                     row(id=2, canonical_name="nfp", display_name="Non Farm Payrolls",
                         actual=150, surprise_normalized=-0.4, **bundle)], None, 3, False)
    assert events[0]["components"][0]["canonical_name"] == "nfp"
    assert events[0]["actual"] == 150 and events[0]["surprise_sigma"] == -0.4


def test_china_trade_bundle_groups_exports_and_imports():
    cn = dict(country_code="CN", currency_code="CNY", country_name="China", surprise_normalized=None)
    rows = [
        row(id=1, canonical_name="exports_cny_yoy", display_name="Exports (YoY, CNY terms)", actual=18.6,
            released_at=datetime(2026, 9, 8, 2, 30, tzinfo=timezone.utc), **cn),
        row(id=2, canonical_name="trade_balance", display_name="Trade Balance", actual=119.1,
            released_at=datetime(2026, 9, 8, 3, tzinfo=timezone.utc), **cn),
        row(id=3, canonical_name="imports_yoy", display_name="Imports (YoY)", actual=28.2,
            released_at=datetime(2026, 9, 8, 3, tzinfo=timezone.utc), **cn),
    ]
    events = _shape(rows, None, 1, False)
    assert len(events) == 1
    event = events[0]
    assert event["name"] == "China trade balance" and event["impact"] == 1
    assert event["actual"] == 119.1
    assert [c["name"] for c in event["components"]][0] == "Trade Balance"
    assert len(event["components"]) == 3
    assert event["released_at"] == datetime(2026, 9, 8, 2, 30, tzinfo=timezone.utc)


def test_china_trade_mappings_resolve():
    from app.ingestion.canonicalizer import Canonicalizer
    canonicalizer = Canonicalizer.from_default_config()
    for event_type, comparison, expected in [
        ("Exports", "yoy", "exports_yoy"), ("Imports", "yoy", "imports_yoy"),
        ("Exports", None, "exports_cny_yoy"), ("Imports", None, "imports_cny_yoy"),
        ("Balance of Trade", None, "trade_balance"), ("Balance of Trade Yuan", None, "trade_balance_cny"),
    ]:
        result = canonicalizer.canonicalize({"type": event_type, "comparison": comparison, "country": "CN",
                                             "date": "2026-09-08 03:00:00", "period": "Aug"})
        assert result is not None and result.canonical_name == expected


@pytest.mark.asyncio
async def test_day_paging_past_and_future():
    class Result:
        def mappings(self): return self
        def all(self): return []
    class Session:
        def __init__(self): self.calls = []
        async def execute(self, _sql, params):
            self.calls.append(params)
            return Result()
    session = Session()
    await list_events(session, date(2020, 1, 1), date(2020, 1, 3))
    assert len(session.calls) == 3
    assert all((p["end_at"] - p["start_at"]).days == 1 for p in session.calls)
    session.calls.clear()
    await list_events(session, date(2030, 1, 1), date(2030, 12, 31), day=date(2030, 5, 1))
    assert len(session.calls) == 1
