from types import SimpleNamespace

from app.services.legacy_unsorted import _category_meter_score, _raw_data, export_analytics_csv, get_overview_kpis, get_actionable_insights
from app.services.news import normalize_news_item


def test_category_meter_normalization_preserves_direction():
    assert _category_meter_score(2.0, 1.0) is not None
    assert _category_meter_score(2.0, -1.0) is not None
    assert round(_category_meter_score(2.0, 1.0), 2) == 1.3
    assert round(_category_meter_score(2.0, -1.0), 2) == -0.1


def test_page_fields_are_removed_at_service_boundary():
    assert _raw_data({"score": 2.5, "released_at": "2026-10-01", "class": "positive",
                      "href": "/country/us", "nested": [{"value": "2.5%", "raw_value": 2.5}]}) == {
        "score": 2.5, "released_at": "2026-10-01", "nested": [{"raw_value": 2.5}],
    }


def test_news_item_keeps_source_fields():
    item = normalize_news_item({"title": "Rate decision", "link": "https://example.com/item",
                                "source": "Desk", "published_at": "2026-10-01T12:00:00Z"})
    assert item is not None
    assert item["title"] == "Rate decision"


def test_analytics_csv_exports_raw_records():
    csv_text = export_analytics_csv({"country_rows": [{"country_code": "US", "release_count": 12}]})
    assert csv_text.startswith("country_rows,country_code,release_count")
    assert ",US,12" in csv_text


def test_overview_kpis_keep_numeric_selection():
    data = get_overview_kpis(
        [{"currency": "USD", "raw_score": 1.0}, {"currency": "EUR", "raw_score": -0.5}],
        {"rows": [{"currency": "GBP", "spread_vs_base_bp": -40.0}]},
        [SimpleNamespace(indicator_id=7, surprise=-2.1)], [{"title": "Headline"}],
    )
    assert data == {
        "strongest_currency": "USD", "strongest_score": 1.0,
        "weakest_currency": "EUR", "weakest_score": -0.5,
        "widest_spread_currency": "GBP", "widest_spread_bp": -40.0,
        "largest_surprise_indicator_id": 7, "largest_surprise": -2.1,
        "headline_count": 1,
    }


def test_actionable_insights_expose_raw_scores_only():
    data = get_actionable_insights(
        [{"currency": "USD", "country_code": "US", "raw_score": 0.2}],
        {"rows": [{"currency": "USD", "change_5d_bp": 8.0}]}, [],
    )
    row = data["bias_rows"][0]
    assert row["score"] == 0.2
    assert row["driver_scores"]["rates"] == 8.0
    assert row["dominant_driver"] == "rates"
    assert "href" not in row and "class" not in row
