from datetime import date, timedelta
from math import cos, sin

from app.services.rates_drivers import calculate_drivers, correlation_changes


def test_uses_changes_even_when_levels_share_an_upward_trend():
    start = date(2026, 1, 1)
    spread = {}
    spot = {}
    spread_level = spot_level = 0.0
    for index in range(100):
        spread_level += 1 + sin(index)
        spot_level += 1 + cos(index)
        day = start + timedelta(days=index)
        spread[day] = spread_level
        spot[day] = spot_level
    assert abs(correlation_changes(spread, spot, 60)) < 0.25
    assert calculate_drivers("EUR/USD", "2Y", spread, spot)["status"] == "weak_link"


def test_no_spot_returns_unavailable_reason():
    assert calculate_drivers("AUD/CAD", "10Y", {date(2026, 1, 1): 50.0}, {}) == {
        "pair": "AUD/CAD", "tenor": "10Y", "status": "unavailable", "reason": "Spot history is absent",
    }


def test_correlation_requires_sixty_changes():
    start = date(2026, 1, 1)
    spread = {start + timedelta(days=index): float(index) for index in range(60)}
    assert correlation_changes(spread, spread, 60) is None
