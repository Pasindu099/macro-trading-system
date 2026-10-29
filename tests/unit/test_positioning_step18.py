"""Golden-dollar calculations, rules, covered episodes and history modes."""
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.services import positioning as pos, positioning_step18 as p, positioning_values as v

DAY = date(2026,10,20)


def fixture():
    rates = {"EUR": 1.2, "GBP": 1.4, "JPY": 1/150, "USD": 100}
    spots = {ccy: pos.SpotSeries([(DAY,price)]) for ccy,price in rates.items()}
    positions = {ccy: {group: [pos.Week(DAY,300,100,1000)] for group in ("leveraged_funds","asset_manager")}
                 for ccy in rates}
    return positions, spots


def test_report_date_dollar_sizes_signs_and_no_dx_subtraction():
    positions, spots = fixture()
    assert v.notional("EUR", positions["EUR"]["leveraged_funds"][0],spots["EUR"]) == 30_000_000
    assert v.notional("USD", positions["USD"]["leveraged_funds"][0],spots["USD"]) == 20_000_000
    yen = 200*12_500_000/150
    assert v.pair_value("USD","JPY",DAY,positions,spots) == pytest.approx(-yen)
    positions["USD"]["leveraged_funds"] = [pos.Week(DAY,999999999,0,1000000000)]
    assert v.pair_value("USD","JPY",DAY,positions,spots) == pytest.approx(-yen)
    assert v.pair_value("EUR","USD",DAY,positions,spots) == 30_000_000
    assert v.pair_value("EUR","GBP",DAY,positions,spots) == pytest.approx(12_500_000)
    assert v.notional("JPY",pos.Week(DAY,1,2,100),spots["JPY"]) < 0
    assert v.notional("EUR",pos.Week(DAY,1,2,100),pos.SpotSeries([(DAY+timedelta(days=1),99)])) is None
    assert v.notional("EUR",pos.Week(DAY,1,2,100),pos.SpotSeries([(DAY-timedelta(days=6),99)])) is None


def test_all_28_pair_notional_conventions():
    from app.services.cot_legacy import specs
    rates = {"EUR":1.2,"GBP":1.4,"JPY":.007,"AUD":.7,"NZD":.6,"CAD":.8,"CHF":1.1,"USD":100}
    positions = {ccy:{"leveraged_funds":[pos.Week(DAY,300,100,1000)]} for ccy in rates}
    spots = {ccy:pos.SpotSeries([(DAY,rate)]) for ccy,rate in rates.items()}
    assert len(pos.config_pairs()) == 28
    for base,quote in pos.config_pairs():
        def dollars(ccy): return 200*specs()["contracts"][ccy]["size"]*rates[ccy]
        expected = -dollars(quote) if base == "USD" else dollars(base) if quote == "USD" else dollars(base)-dollars(quote)
        assert v.pair_value(base,quote,DAY,positions,spots) == pytest.approx(expected)


@pytest.mark.asyncio
async def test_simple_cards_sort_disagreement_before_extremes(monkeypatch):
    def series(nets): return [pos.Week(DAY-timedelta(weeks=len(nets)-1-i),max(n,0),max(-n,0),1000) for i,n in enumerate(nets)]
    positions = {"EUR":{"leveraged_funds":series([1,2,3]),"asset_manager":series([3,2,1])},
                 "GBP":{"leveraged_funds":series([1,2,3]),"asset_manager":series([1,2,3])}}
    monkeypatch.setattr(pos,"load_positions",AsyncMock(return_value=positions))
    monkeypatch.setattr(pos,"load_spot",AsyncMock(return_value={}))
    result = await p.big_traders("52w")
    assert result["simple"][0]["status"] == "They disagree" and result["simple"][0]["currency"] == "EUR"
    assert result["simple"][1]["currency"] == "GBP"


@pytest.mark.parametrize("percentile,side,extreme", [(15,"against",True),(35,"against",False),(36,None,False),
    (64,None,False),(65,"on",False),(85,"on",True),(None,None,False)])
def test_side_extreme_boundaries(percentile,side,extreme):
    assert p.side(percentile) == side and p.extreme(percentile) == extreme


@pytest.mark.parametrize("net,prior,label", [(-80,-100,"cutting the bet"),(-120,-100,"adding to the bet"),
    (80,100,"cutting the bet"),(120,100,"adding to the bet"),(10,-10,"switched sides"),
    (0,20,"closed the bet"),(20,0,"opened the bet"),(None,10,"unavailable")])
def test_adding_cutting_relative_to_sign(net,prior,label):
    assert p.bet_change(net,prior) == label


def test_simple_status_priority_and_missing():
    def snap(percentile): return {"percentile": percentile, "report_date": str(DAY)}
    assert p.simple_card("EUR",snap(90),snap(10))["status"] == "They disagree"
    assert p.simple_card("EUR",snap(90),snap(80))["rank"] == 1
    assert p.simple_card("EUR",snap(75),snap(50))["status"] == "Hedge funds only"
    assert p.simple_card("EUR",snap(50),snap(25))["status"] == "Slow money only"
    assert p.simple_card("EUR",snap(50),snap(50))["status"] == "No strong bets"
    assert p.simple_card("EUR",None,snap(50))["status"] == "Inputs unavailable"
    assert p.simple_card("EUR",snap(None),snap(50))["status"] == "Inputs unavailable"


def test_squeeze_half_std_threshold_and_missing_evidence():
    assert pos.squeeze_status(90,[100,110,120],-.49,1)["status"] == "working"
    assert pos.squeeze_status(90,[100,110,120],-.5,1)["status"] == "medium"
    assert pos.squeeze_status(90,[120,110,100],-.5,1)["status"] == "high"
    assert pos.squeeze_status(10,[-120,-110,-100],.5,1)["status"] == "high"
    assert pos.squeeze_status(90,[100,110,120],-5,None)["against_crowd"] is None


@pytest.mark.asyncio
async def test_single_market_oi_sign_swap_and_cross_fallback(monkeypatch):
    positions,spots = fixture()
    monkeypatch.setattr(v,"load_group_positions",AsyncMock(return_value=positions))
    monkeypatch.setattr(pos,"load_spot",AsyncMock(return_value=spots))
    monkeypatch.setattr(v,"load_pair_spot",AsyncMock(return_value=pos.SpotSeries([(DAY,1.2/1.4)])))
    result = await v.history("USD/JPY",units="pct_oi")
    row = result["history"][0]
    assert row["net"] == -20 and row["long"] == 10 and row["short"] == 30
    assert row["price"] == pytest.approx(150) and row["open_interest"] == 1000
    cross = await v.history("EUR/GBP",units="pct_oi")
    assert cross["units"] == "usd" and "two futures markets" in cross["explanation"]
    assert cross["history"][0]["net"] == pytest.approx(.0125)
    assert cross["history"][0]["net_pct_oi"] is None
    assert cross["history"][0]["long"] is None
    assert pos._years("52w") == 1


@pytest.mark.asyncio
async def test_pair_api_week_cap_and_legacy_group_validation(monkeypatch):
    from types import SimpleNamespace
    from app.api.routes import positioning as routes
    positions,spots = fixture()
    monkeypatch.setattr(routes,"get_settings",lambda: SimpleNamespace(auth_enabled=False))
    monkeypatch.setattr(pos,"load_positions",AsyncMock(return_value=positions))
    monkeypatch.setattr(pos,"load_spot",AsyncMock(return_value=spots))
    app = FastAPI(); app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        response = await client.get("/api/positioning/pair/USD/JPY?weeks=1")
        assert response.status_code == 200
        result = response.json()
        assert not result["implied"] and len(result["groups"]["leveraged_funds"]["history"]) == 1
        assert result["groups"]["leveraged_funds"]["history"][0]["net_usd_bn"] < 0
        for query in ("weeks=0","weeks=1001"):
            assert (await client.get("/api/positioning/pair/USD/JPY?"+query)).status_code == 422
        assert (await client.get("/api/positioning/history/JPY?group=invalid")).status_code == 422


def test_extreme_entries_count_once_and_use_medians(monkeypatch):
    # Alternate covered extreme entries with normal weeks; each entry has its own 8W outcome.
    weeks = [pos.Week(date(2010,1,5)+timedelta(weeks=i),1,0,100) for i in range(70)]
    percentiles = [10 if i%10 in (0,1,2) else 50 for i in range(70)]
    percentiles[-1] = 10
    monkeypatch.setattr(pos,"rolling_percentiles",lambda *args: percentiles)
    spot = pos.SpotSeries([(w.report_date,100+i) for i,w in enumerate(weeks)])
    result = p.extreme_history(weeks,spot)
    assert result["state"] == "available" and result["count"] == 7
    assert len({row["date"] for row in result["episodes"]}) == 7
    from statistics import median
    assert result["median_8w_pct"] == median(row["move_8w_pct"] for row in result["episodes"])
    assert result["up"] == result["against_crowd"] == 7
    percentiles[-1] = 50
    result = p.extreme_history(weeks,spot)
    assert result["state"] == "not_extreme" and not result["episodes"]


def test_too_few_episodes_hide_comparisons(monkeypatch):
    weeks = [pos.Week(DAY-timedelta(weeks=20-i),1,0,100) for i in range(21)]
    monkeypatch.setattr(pos,"rolling_percentiles",lambda *args: [10]*21)
    spot = pos.SpotSeries([(w.report_date,100+i) for i,w in enumerate(weeks)])
    result = p.extreme_history(weeks,spot)
    assert result["state"] == "too_few" and result["episodes"] == [] and result["median_8w_pct"] is None


def test_missing_percentiles_do_not_create_a_false_new_entry():
    assert p.episode_entries([10,10,None,10,50,10],lambda value:value <= 15) == [0,5]


@pytest.mark.asyncio
async def test_small_short_vs_heavy_short_wording_and_missing_oi(monkeypatch):
    weeks = [pos.Week(DAY-timedelta(weeks=2-i),0,n,1000) for i,n in enumerate([100,50,10])]
    positions = {"JPY":{"leveraged_funds":weeks}}
    spots = {"JPY":pos.SpotSeries([(w.report_date,.007) for w in weeks])}
    monkeypatch.setattr(v,"load_group_positions",AsyncMock(return_value=positions))
    monkeypatch.setattr(pos,"load_spot",AsyncMock(return_value=spots))
    result = await v.history("JPY",units="pct_oi")
    assert "rarely been this small" in result["percentile_note"]
    for w,n in zip(weeks,[10,50,100]): w.short = n
    result = await v.history("JPY",units="pct_oi")
    assert "larger bet against" in result["percentile_note"]
    weeks[-1].open_interest = 0
    result = await v.history("JPY",units="pct_oi")
    assert result["history"][-1]["net"] is None and result["state"] == "unavailable"
