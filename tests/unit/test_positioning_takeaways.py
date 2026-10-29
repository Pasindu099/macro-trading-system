"""Live templates: everyday units, sign-sensitive wording and writing limits."""
import re
from datetime import date, timedelta
import pytest
from app.services import takeaways


def examples():
    today = date.today()
    history = [{"report_date":str(today-timedelta(weeks=4-i)),"net":-4+i*.5,"price":100+i} for i in range(5)]
    card = {"currency":"JPY","stale":False,"headline":"Hedge funds are heavily betting against JPY and the yen against the dollar is moving against them."}
    return {
        "summary":{"cards":[card]},
        "squeeze":{"cards":[card]},
        "crowding":{"bars":[{"currency":"JPY","groups":{"leveraged_funds":{"net_usd":-4e9}}}],
                    "simple":[{"currency":"AUD","status":"They disagree"}]},
        "flows":{"window":"1W","rows":[{"currency":"JPY","change_usd":1e9,"net":-3e9,"action":"cutting the bet"}]},
        "price":{"instrument":"JPY","group_label":"Hedge funds","units":"usd","history":history},
        "extremes":{"reading":"Hedge funds held a bet this heavily against JPY nine times since 2010. In five cases the yen against the dollar rose over eight weeks and in four it fell.",
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
    assert "a $4.00bn bet against JPY" in takeaways.crowding({"state":"available",**examples()["crowding"]})["now"]
    text = takeaways.flows({"state":"available",**examples()["flows"]})["now"]
    assert "cut $1.00bn from their bet against JPY" in text and "$1.00bn" in text
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

@pytest.mark.parametrize("panel", list(takeaways.REGISTRY))
def test_positioning_plain_bet_language(panel):
    result = takeaways.REGISTRY[panel]({"state":"available", **examples()[panel]})
    for key in ("shows", "now", "watch"):
        assert not re.search(r"\b(short|long(?!-term))\b|net position|the price", result[key], re.I)


def test_changes_levels_and_named_price():
    from app.services.positioning_values import price_name
    assert "a $4.00bn bet against JPY" in takeaways.crowding({"state":"available", **examples()["crowding"]})["now"]
    assert "cut $1.00bn from their bet against JPY" in takeaways.flows({"state":"available", **examples()["flows"]})["now"]
    text = takeaways.price({"state":"available", **examples()["price"]})["now"]
    assert "cut $2.00bn from their bet against JPY" in text
    assert "the yen against the dollar rose 4.0%" in text
    assert price_name("USD") == "the dollar index"
    assert price_name("USD/JPY") == "the dollar against the yen"
    assert price_name("EUR/JPY") == "the euro against the yen"

@pytest.mark.parametrize('start,end,expected', [
    (2,4,'added $2.00bn to their bet on'),
    (-4,-2,'cut $2.00bn from their bet against'),
    (4,2,'cut $2.00bn from their bet on'),
    (-2,-4,'added $2.00bn to their bet against'),
    (-2,2,'switched to a bet on JPY, a change of $4.00bn'),
    (2,0,'closed their bet on JPY, a change of $2.00bn'),
])
def test_price_changes_follow_bet_side(start,end,expected):
    data = examples()['price']
    for i,row in enumerate(data['history']): row['net'] = start+(end-start)*i/4
    assert expected in takeaways.price({'state':'available', **data})['now']
