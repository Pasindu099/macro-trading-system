"""Step 10 Part C: priced meeting branches and unpriced situation tails."""

from datetime import UTC, datetime

from app.services.scenarios import build_scenarios


def verdict():
    return {"status": "available", "bias": "bearish", "horizon": "1M", "active_situations": []}


def market():
    return {"bank": "FED", "meetings": [{"meeting_at": datetime(2026, 10, 28, tzinfo=UTC),
                                          "market_data_available": True,
                                          "hold_prob": .45, "hike_prob": .35, "cut_prob": .20}]}


def test_three_meeting_probabilities_sum_to_100_and_directions_follow_bias():
    result = build_scenarios("USD", verdict(), market(), ["CPI", "Payrolls"], "running_cold")
    rows = result["scenarios"]
    assert [row["probability_pct"] for row in rows] == [45.0, 35.0, 20.0]
    assert sum(row["probability_pct"] for row in rows) == 100
    assert [row["direction"] for row in rows] == ["down", "flat", "down"]
    assert "CPI, Payrolls" in rows[0]["trigger"] and "running_cold" in rows[0]["trigger"]


def test_high_severity_tail_is_not_priced_and_uses_evidence():
    view = verdict()
    view["active_situations"] = [
        {"situation_id": "fr_fiscal_stress", "scope_key": "EUR", "severity": "high", "effect": -1,
         "evidence": {"current": {"oat_bund_10y_bp": 90.0}}},
        {"situation_id": "correlation_break", "scope_key": "EUR/USD", "severity": "medium", "effect": 0},
    ]
    result = build_scenarios("EUR", view, market(), [], "unavailable")
    tail = result["scenarios"][3]
    assert len(result["scenarios"]) == 4
    assert tail["label"] == "Fiscal tail" and tail["pricing_status"] == "not priced"
    assert tail["probability_pct"] is None and "90.0" in tail["trigger"]


def test_missing_market_data_never_generates_probabilities():
    data = market()
    data["meetings"][0]["market_data_available"] = False
    result = build_scenarios("EUR", verdict(), data, [], "unavailable")
    assert not result["market_probabilities_available"]
    assert all(row["probability_pct"] is None for row in result["scenarios"])
    assert all(row["pricing_status"] == "unavailable" for row in result["scenarios"])
