"""Step 10 Part B: weighted verdict and conviction rules."""

from app.services.verdict import _component, score_verdict


def components():
    return {
        "macro": _component(1.5), "cb_tracking": _component(1.0),
        "gap": _component(1.0), "curve": _component(0.5),
        "positioning": _component(0.0, raw={"percentile": 50}),
        "situations": _component(0.0),
    }


def test_weighted_score_bias_thesis_and_why():
    result = score_verdict("USD", components(), [])
    assert result["status"] == "available" and result["bias"] == "bullish"
    assert result["score"] == 0.95
    assert result["conviction"] == "high" and result["agreement"] == 1.0
    assert result["dominant_driver"] == "data"
    assert result["thesis"].count(".") == 2
    assert set(result["why"]) == set(components())
    assert result["why"]["macro"]["contribution"] == 0.525


def test_opposition_crowding_and_high_severity_reduce_conviction():
    inputs = components()
    inputs["positioning"] = _component(-0.5, raw={"percentile": 90})
    inputs["curve"] = _component(-0.2)
    risk = {"situation_id": "risk_off", "severity": "high", "scope": "currency", "scope_key": "GLOBAL"}
    result = score_verdict("USD", inputs, [risk])
    assert result["crowding_penalty"] and result["high_severity_penalty"]
    assert result["conviction"] == "low"
    assert result["dominant_driver"] == "geopolitics / risk sentiment"
    assert result["main_risk"] == "crowded positioning limits upside"


def test_unavailable_inputs_excluded_without_reweighting():
    inputs = {name: _component(None, reason="missing") for name in components()}
    assert score_verdict("EUR", inputs, [])["status"] == "unavailable"
    inputs["macro"] = _component(-2.0)
    result = score_verdict("EUR", inputs, [])
    assert result["score"] == -0.7 and result["bias"] == "bearish"
    assert result["why"]["gap"]["contribution"] is None
    assert result["conviction"] == "moderate"
