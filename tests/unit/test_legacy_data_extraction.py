from app.services.legacy_unsorted import _category_meter_score, _raw_data
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
