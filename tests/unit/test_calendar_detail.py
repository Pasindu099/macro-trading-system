from datetime import datetime, timedelta, timezone

import pytest

from app.services import calendar_detail
from app.services.calendar_detail import chart_data, shape_history, shape_reactions, why_it_matters
from app.services.event_reactions import compute_stats, direction_currency, pairs_for, signed_move_pips


def _history_row(day, actual, first, consensus=1.0, sigma=0.5, period=None):
    return {"id": day, "released_at": datetime(2026, 1, day, 13, tzinfo=timezone.utc),
            "period": period, "actual": actual, "first_actual": first,
            "consensus": consensus, "sigma": sigma}


def test_history_first_print_vs_revised_and_upcoming_empty_slot():
    rows = [_history_row(3, None, None, consensus=2.5, sigma=None),  # upcoming, newest first
            _history_row(2, 2.2, 2.0), _history_row(1, 1.5, 1.5)]
    history = shape_history(rows)
    assert [row["release_id"] for row in history] == [1, 2, 3]
    assert history[1]["revised"] is True and history[1]["first_print"] == 2.0 and history[1]["actual"] == 2.2
    assert history[0]["revised"] is False
    chart = chart_data(history)
    assert chart["actual"] == [1.5, 2.2, None]
    assert chart["first_print"] == [1.5, 2.0, None]
    assert chart["consensus"][-1] == 2.5
    assert chart["labels"][-1] == "2026-01-03"


def test_signed_move_expressed_for_event_currency():
    assert signed_move_pips("EUR/USD", 1.1000, 1.1050, "EUR") == pytest.approx(50)
    assert signed_move_pips("EUR/USD", 1.1000, 1.1050, "USD") == pytest.approx(-50)
    assert signed_move_pips("USD/JPY", 150.00, 149.50, "JPY") == pytest.approx(50)
    # Calendar-only CNY reads through the AUD base.
    assert direction_currency("AUD/USD", "CNY") == "AUD"
    assert signed_move_pips("AUD/USD", 0.6600, 0.6620, "CNY") == pytest.approx(20)
    assert pairs_for("CNY") == ["AUD/USD", "NZD/USD"]
    assert pairs_for("USD")[0] == "EUR/USD" and len(pairs_for("EUR")) == 3


def test_reaction_stats_hit_rate_and_beat_miss_split():
    moves = [(1.5, 30.0), (2.0, 10.0), (-1.2, -20.0), (0.6, -5.0), (0.3, 40.0),
             (-0.7, -8.0), (None, 12.0), (0.9, 0.0)]
    stats = compute_stats(moves)
    assert stats["sample_n"] == 8
    assert stats["avg_abs_move_pips"] == pytest.approx(round(125 / 8, 2))
    assert (stats["beat_n"], stats["beat_avg_pips"]) == (2, 20.0)
    assert (stats["miss_n"], stats["miss_avg_pips"]) == (1, -20.0)
    # |σ| >= 0.5 only: 1.5, 2.0, -1.2, 0.6, -0.7, 0.9 → hits 1.5, 2.0, -1.2, -0.7; zero move is no hit.
    assert stats["hit_n"] == 6 and stats["hit_rate"] == pytest.approx(round(4 / 6, 4))


def test_reaction_stats_hidden_below_minimum_releases():
    stats = {"EUR/USD": compute_stats([(1.0, 5.0)] * 7) | {"pair": "EUR/USD"},
             "USD/JPY": compute_stats([(1.0, 5.0)] * 8) | {"pair": "USD/JPY"}}
    moves = [{"release_id": 9, "released_at": datetime(2026, 1, 9, tzinfo=timezone.utc), "period": "Dec",
              "sigma": 1.0, "pair": "EUR/USD", "move_pips": 12.0}]
    shaped = shape_reactions(moves, stats, ["EUR/USD", "USD/JPY", "GBP/USD"], 9)
    by_pair = {row["pair"]: row for row in shaped["summary"]}
    assert by_pair["EUR/USD"]["stats"] is None and by_pair["EUR/USD"]["sample_n"] == 7
    assert by_pair["USD/JPY"]["stats"]["sample_n"] == 8
    assert by_pair["GBP/USD"]["sample_n"] == 0
    assert by_pair["EUR/USD"]["this_move"] == 12.0


def test_why_it_matters_for_calendar_only_and_tracked():
    assert why_it_matters("CNY", [{"name": "Trade Balance"}], {}) == "Affects AUD, NZD via China demand."
    text = why_it_matters("USD", [{"name": "CPI (YoY)", "canonical_name": "cpi_headline_yoy"}],
                          {"USD": "inflation above target"})
    assert text.startswith("Fed dual mandate: inflation above target")


@pytest.mark.asyncio
async def test_event_detail_header_components_and_links(monkeypatch):
    released = datetime.now(timezone.utc) + timedelta(days=1)
    components = [
        {"release_id": 11, "indicator_id": 5, "canonical_name": "nfp", "name": "Non Farm Payrolls",
         "actual": None, "consensus": 150.0, "previous": 120.0, "revised_from": 110.0,
         "surprise_sigma": None, "source_status": "scheduled", "mapped": True},
        {"release_id": 12, "indicator_id": 6, "canonical_name": "unemployment_rate", "name": "Unemployment Rate",
         "actual": None, "consensus": 4.1, "previous": 4.2, "revised_from": None,
         "surprise_sigma": None, "source_status": "scheduled", "mapped": True},
    ]
    event = {"id": "11", "released_at": released, "country_code": "US", "currency_code": "USD",
             "country_name": "United States", "name": "US_NFP_DAY", "impact": 1, "components": components,
             "actual": None, "consensus": 150.0, "previous": 120.0, "revised_from": 110.0,
             "surprise_sigma": None, "source_status": "scheduled"}

    async def fake_find(session, release_id):
        return event if release_id == 12 else None

    class Result:
        def __init__(self, rows): self.rows = rows
        def mappings(self): return self
        def all(self): return self.rows

    class Session:
        async def execute(self, sql, params=None):
            if "event_reaction_stats" in str(sql):
                return Result([])
            if "event_reaction_notes" in str(sql):
                return Result([])
            if "fx_spot_observations" in str(sql):
                return Result([])
            return Result([_history_row(1, 140.0, 135.0)])

    monkeypatch.setattr(calendar_detail, "find_event", fake_find)
    detail = await calendar_detail.event_detail(Session(), 12, tracking={"USD": "inflation near target"})
    assert detail["upcoming"] is True
    assert detail["event"]["name"] == "Unemployment Rate"
    assert detail["event"]["revised_from"] is None and detail["event"]["consensus"] == 4.1
    assert detail["single_indicator"] is False and len(detail["event"]["components"]) == 2
    assert detail["history"][0]["first_print"] == 135.0
    assert detail["links"]["desk"] == "/desks/USD"
    assert detail["links"]["event_log"] == "/calendar/event-log/new?release_id=12"
    assert [pair["pair"] for pair in detail["links"]["pairs"]] == ["EUR/USD", "USD/JPY", "GBP/USD"]
    assert await calendar_detail.event_detail(Session(), 99) is None
