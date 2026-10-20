from app.services import pairs_service


def test_pair_order_and_filter_groups():
    rows = [{"pair": item["pair"]} for item in pairs_service.pair_config()]
    assert len(rows) == 28
    assert [row["pair"] for row in rows[:7]] == ["EUR/GBP", "EUR/AUD", "EUR/NZD", "EUR/USD",
                                                    "EUR/CAD", "EUR/CHF", "EUR/JPY"]
    assert len(pairs_service.filter_rows(rows, "usd")) == 7
    assert len(pairs_service.filter_rows(rows, "jpy")) == 6
    assert [row["pair"] for row in pairs_service.filter_rows(rows, search="eurusd")] == ["EUR/USD"]


def test_sort_pending_last_and_overall_magnitude():
    rows = [{"pair": "A/B", "overall_score": -2, "policy_gap": None},
            {"pair": "C/D", "overall_score": 1, "policy_gap": 5},
            {"pair": "E/F", "overall_score": None, "policy_gap": -7}]
    assert [row["pair"] for row in pairs_service.sort_rows(rows)] == ["A/B", "C/D", "E/F"]
    assert [row["pair"] for row in pairs_service.sort_rows(rows, "policy_gap")] == ["C/D", "E/F", "A/B"]
    assert [row["pair"] for row in pairs_service.sort_rows(rows, "policy_gap", False)] == ["E/F", "C/D", "A/B"]
