"""Live templates: everyday units, sign-sensitive wording and writing limits."""
import re
from datetime import date, timedelta
import pytest
from app.services import takeaways


def examples():
    today = date.today()
    history = [{"report_date":str(today-timedelta(weeks=4-i)),"net":-4+i*.5,"price":100+i} for i in range(5)]
    card = {"currency":"JPY","stale":False,"headline":"Hedge funds are heavily betting against JPY and the price is moving against them."}
    return {
        "summary":{"cards":[card]},
        "squeeze":{"cards":[card]},
        "crowding":{"bars":[{"currency":"JPY","groups":{"leveraged_funds":{"net_usd":-4e9}}}],
                    "simple":[{"currency":"AUD","status":"They disagree"}]},
        "flows":{"window":"1W","rows":[{"currency":"JPY","change_usd":1e9,"net":-3e9,"action":"cutting the bet"}]},
        "price":{"instrument":"JPY","group_label":"Hedge funds","units":"usd","history":history},
        "extremes":{"reading":"Hedge funds were this short JPY nine times since 2010. In five cases the currency rose over eight weeks and in four it fell.",
                    "outcome_range_pct":{"min":-2.7,"max":3.9}},
        "retail":{"retail_state":"ok","pairs":[{"pair":"EUR/USD","cot_implied":50,"retail_long_pct":20,"signal":"opposite sides"}]},
    }


@pytest.mark.parametrize("panel",list(takeaways.REGISTRY))
def test_live_three_parts_meet_writing_rules(panel):
    result = takeaways.REGISTRY[panel]({"state":"available",**examples()[panel]})
    for key in ("shows","now","watch"):
        text = result[key]
        assert not re.search(r"\b(will|likely|expected to|forecast)\b",text,re.I)
        assert len(re.split(r"[.!?]\s+",text.strip())) <= 2
        assert not re.search(r"\d[\d,]*\s+contracts",text)
        assert "not available yet" not in text
        assert "".join(segment["text"] for segment in result["parts"][key]) == text


def test_live_numbers_and_short_covering_wording():
    assert "$4.00bn short" in takeaways.crowding({"state":"available",**examples()["crowding"]})["now"]
    text = takeaways.flows({"state":"available",**examples()["flows"]})["now"]
    assert "cut their JPY short bet" in text and "$1.00bn" in text
    text = takeaways.price({"state":"available",**examples()["price"]})["now"]
    assert "four weeks" in text and "$2.00bn" in text and "rose 4.0%" in text


@pytest.mark.parametrize("state",["unavailable","stale"])
def test_missing_and_stale_never_reuse_current_reading(state):
    for panel,values in examples().items():
        result = takeaways.REGISTRY[panel]({**values,"state":state})
        assert "unavailable" in result["now"] and "JPY" not in result["now"]


def test_too_few_episodes_and_missing_retail_are_explicit():
    assert "Fewer than five" in takeaways.extremes({"state":"too_few","message":"Fewer than five covered cases; comparison unavailable."})["now"]
    assert "unavailable" in takeaways.retail({"state":"available","retail_state":"not_configured"})["now"]
