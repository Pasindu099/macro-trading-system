from types import SimpleNamespace
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

import app.auth as auth
from app.main import app
from app.services import overview
from app.services import desks


def _client(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    return TestClient(app)


def test_each_overview_panel_renders_data_and_pending(monkeypatch):
    client = _client(monkeypatch)
    desks.cache_clear()
    data = {
        "hero": {"state": "ok", "driver": "Economic data", "theme": "Data drives FX.", "hierarchy": [
            {"name": name, "state": "Supporting", "evidence": "Pending"} for name in overview.HIERARCHY]},
        "situations": {"state": "ok", "rows": [{"href": "/desks/EUR", "name": "Fiscal stress", "scope_key": "EUR", "severity": "high", "started_at": __import__("datetime").datetime(2026, 1, 1)}]},
        "news": {"state": "ok", "rows": [], "ai_paused": True},
        "ranking": {"state": "ok", "rows": [{"currency": "USD", "score": None, "score_bar": None,
            "bias": None, "partial": False, "cb_regime": None, "gap": "pending", "cot": None,
            "cot_label": "pending", "thesis": None, "enabled": False}]},
        "central-banks": {"state": "ok", "rows": []},
        "pairs": {"state": "ok", "codes": overview.PAIR_ORDER, "matrix": [], "lens": "overall",
                  "path": "data", "group": "all", "top": [], "widest": [], "concentration": "No scored pairs."},
        "events": {"state": "ok", "rows": []},
    }
    for panel_id, ctx in data.items():
        async def builder(result=ctx, **_kwargs):
            return result
        monkeypatch.setitem(overview.PANELS, panel_id, builder)
        response = client.get(f"/overview/panels/{panel_id}")
        assert response.status_code == 200
        assert "<h2>" in response.text or "<h1>G10 Overview</h1>" in response.text
        desks.cache_clear()
        async def pending(**_kwargs):
            return {"state": "pending", "message": "Desk pending"}
        monkeypatch.setitem(overview.PANELS, panel_id, pending)
        response = client.get(f"/overview/panels/{panel_id}")
        assert response.status_code == 200
        assert "Desk pending" in response.text
        assert "0.5" not in response.text
        desks.cache_clear()


def test_panel_cache_and_error_state(monkeypatch):
    client = _client(monkeypatch)
    desks.cache_clear()
    calls = 0
    async def builder():
        nonlocal calls
        calls += 1
        return {"state": "empty", "message": "Nothing yet"}
    monkeypatch.setitem(overview.PANELS, "events", builder)
    assert "Nothing yet" in client.get("/overview/panels/events").text
    assert "Nothing yet" in client.get("/overview/panels/events").text
    assert calls == 1
    desks.cache_clear()
    async def broken():
        raise RuntimeError("source offline")
    monkeypatch.setitem(overview.PANELS, "events", broken)
    assert "could not load" in client.get("/overview/panels/events").text
    monkeypatch.setitem(overview.PANELS, "events", builder)
    assert "Nothing yet" in client.get("/overview/panels/events").text
    assert calls == 2
    desks.cache_clear()


def test_pair_score_and_policy_lens_are_distinct():
    rows = overview._pair_rows({"EUR": 1.2, "USD": 0.2, "JPY": -0.4}, [
        {"pair": "EUR/USD", "base": "EUR", "quote": "USD"},
        {"pair": "USD/JPY", "base": "USD", "quote": "JPY"},
    ])
    assert rows[0] == {"pair": "EUR/USD", "overall": 1.0, "policy": "available"}
    assert rows[1]["policy"] == "pending"


def test_matrix_convention_groups_and_top_five_concentration(monkeypatch):
    async def ranking():
        return {"rows": [{"currency": currency, "score": float(8 - index)}
                         for index, currency in enumerate(overview.PAIR_ORDER)]}
    monkeypatch.setattr(overview, "currency_ranking", ranking)
    result = __import__("asyncio").run(overview.pair_map())
    cells = [cell for row in result["matrix"] for cell in row["cells"] if cell["kind"] == "pair"]
    assert len(cells) == 28
    assert result["matrix"][0]["cells"][4]["pair"] == "EUR/USD"
    assert result["matrix"][4]["cells"][0]["kind"] == "lower"
    assert "Concentrated:" in result["concentration"]
    usd = __import__("asyncio").run(overview.pair_map(group="usd"))
    assert len(usd["top"]) == 5
    assert sum(cell["in_group"] for row in usd["matrix"] for cell in row["cells"]
               if cell["kind"] == "pair") == 7
    policy = __import__("asyncio").run(overview.pair_map(lens="policy", path="gap"))
    eligible = [cell for row in policy["matrix"] for cell in row["cells"]
                if cell["kind"] == "pair" and cell["eligible"]]
    assert [cell["pair"] for cell in eligible] == ["EUR/USD"]
    assert eligible[0]["value"] is None
    assert policy["top"] == []


def test_pair_filter_requests_use_distinct_cache_entries(monkeypatch):
    calls = []
    async def builder(*, lens, path, group):
        calls.append((lens, path, group))
        return {"state": "ok", "codes": overview.PAIR_ORDER, "matrix": [],
                "lens": lens, "path": path, "group": group, "top": [], "widest": [],
                "concentration": "No scored pairs."}
    monkeypatch.setitem(overview.PANELS, "pairs", builder)
    desks.cache_clear()
    client = _client(monkeypatch)
    first = client.get("/overview/panels/pairs?lens=overall&group=usd")
    second = client.get("/overview/panels/pairs?lens=policy&path=gap&group=usd")
    assert first.status_code == second.status_code == 200
    assert calls == [("overall", "data", "usd"), ("policy", "gap", "usd")]
    assert 'aria-pressed="true"' in second.text
    desks.cache_clear()


def test_ranking_sorts_by_verdict_score_and_labels_partial(monkeypatch):
    async def board():
        return {"rows": [{"currency": "EUR", "overall_score": -0.2, "date": None},
                         {"currency": "USD", "overall_score": 0.6, "date": None},
                         {"currency": "AUD", "overall_score": 0.8, "date": None}]}
    async def crowding():
        return {"currencies": [{"currency": "AUD", "leveraged_funds": {"percentile": 50}}]}
    async def verdict_for(currency):
        return {"status": "available", "score": .1 if currency == "USD" else -.2,
                "bias": "bullish" if currency == "USD" else "bearish", "thesis": "A clear view."}
    async def curve(_country, _window):
        return {"regime": {"status": "available", "label": "bear_steepener"} if _country == "AU"
                else {"status": "unavailable"}}
    async def regimes():
        return {currency: {"regime": "holding"} for currency in overview.CURRENCIES}
    monkeypatch.setattr(overview, "_board", board)
    monkeypatch.setattr(overview.positioning, "get_crowding", crowding)
    monkeypatch.setattr(overview.verdict, "get_verdict", verdict_for)
    monkeypatch.setattr(overview, "get_curve", curve)
    monkeypatch.setattr(overview, "_policy_regimes", regimes)
    rows = __import__("asyncio").run(overview.currency_ranking())["rows"]
    assert [row["currency"] for row in rows[:3]] == ["AUD", "USD", "EUR"]
    assert rows[0]["partial"] and rows[0]["score"] > rows[1]["score"]
    assert not rows[1]["partial"]
    assert rows[3]["score"] is None


def test_partial_thesis_and_gap_labels():
    only_macro = overview._partial_verdict("GBP", .4, None, None)
    assert only_macro["thesis"].startswith("Macro State indicates")
    assert overview._gap_label("USD", {"why": {"gap": {"raw": {"gap_bp": -3}}}}) == "-3bp dovish"
    assert overview._gap_label("EUR", {"why": {"gap": {"raw": {"direction": "neutral"}}}}) == "neutral: projections at target"


def test_news_uses_recent_investinglive_items_only(monkeypatch):
    now = datetime.now(UTC)
    class Result:
        def mappings(self):
            return self
        def all(self):
            return [{"headline": title, "source": "investinglive.com", "url": None,
                     "seen_at": timestamp, "severity": None} for title, timestamp in (
                         ("Fed rate outlook", now - timedelta(hours=1)),
                         ("USD CPI tomorrow", now + timedelta(hours=1)),
                         ("Fed rate last week", now - timedelta(days=7)),
                         ("Weekend lifestyle update", now - timedelta(minutes=5)))]
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def execute(self, query, params):
            sql = str(query)
            assert "news_alerts" not in sql
            assert "source = 'investinglive.com'" in sql
            assert "published_at <= :now" in sql
            assert params["since"] < now <= params["now"]
            return Result()
    monkeypatch.setattr(overview, "get_sessionmaker", lambda: lambda: Session())
    async def episodes(*, active):
        assert active
        return []
    monkeypatch.setattr(overview.situations, "get_situation_episodes", episodes)
    rows = __import__("asyncio").run(overview.moving_markets())["rows"]
    assert len(rows) == 1
    assert rows[0]["headline"] == "Fed rate outlook"
    assert rows[0]["currencies"] == ["USD"]
    assert rows[0]["tag"] == "CB policy divergence"


def test_regime_map_renders_calendar_string(monkeypatch):
    async def regimes():
        return {currency: {"regime": "holding"} for currency in overview.CURRENCIES}
    async def meetings(_bank, _limit):
        return [{"meeting_dt": "2026-11-01T12:00:00+00:00"}]
    async def verdict_for(_currency):
        return {"why": {"gap": {"raw": {"direction": "neutral"}}}}
    async def pricing(_bank, _session):
        return {"market_data": {"available": False}}
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
    monkeypatch.setattr(overview, "_policy_regimes", regimes)
    monkeypatch.setattr(overview, "get_upcoming_meetings", meetings)
    monkeypatch.setattr(overview.verdict, "get_verdict", verdict_for)
    monkeypatch.setattr(overview, "get_rate_probability_view", pricing)
    monkeypatch.setattr(overview, "get_sessionmaker", lambda: lambda: Session())
    client = _client(monkeypatch)
    desks.cache_clear()
    response = client.get("/overview/panels/central-banks")
    assert response.status_code == 200
    assert "could not load" not in response.text
    assert "01 Nov 2026" in response.text
    assert response.text.count('class="current"') == 8
    desks.cache_clear()


def test_priced_move_requires_recent_market_data():
    today = date(2026, 10, 12)
    view = {"market_data": {"available": True, "curve_date": date(2026, 10, 10)},
            "meetings": [{"market_data_available": True, "delta_bps": -12.5}]}
    assert overview._priced_move(view, "USD", today) == "-12bp"
    assert overview._priced_move(view, "EUR", today) == "-12bp approx."
    view["market_data"]["curve_date"] = date(2026, 10, 8)
    assert overview._priced_move(view, "USD", today) == "n/a"
    view["market_data"]["curve_date"] = date(2026, 10, 13)
    assert overview._priced_move(view, "EUR", today) == "n/a"


def test_dominance_override_and_share_threshold():
    tiers, lead = overview._dominance_tiers({"rates": .26, "data": .40}, risk_off=False,
        geo_count=2, vix=16, vix_change=0, stretched=[], squeeze_against_crowd=False)
    assert lead == "Economic data"
    assert len(tiers) == 6
    _, lead = overview._dominance_tiers({"rates": .24, "data": .24}, risk_off=False,
        geo_count=0, vix=16, vix_change=0, stretched=[], squeeze_against_crowd=False)
    assert lead == "No single driver"
    _, lead = overview._dominance_tiers({"rates": .9, "data": .01}, risk_off=True,
        geo_count=2, vix=30, vix_change=5, stretched=[], squeeze_against_crowd=False)
    assert lead == "Geopolitical shock"
    _, lead = overview._dominance_tiers({"rates": .9}, risk_off=True,
        geo_count=1, vix=30, vix_change=5, stretched=[], squeeze_against_crowd=False)
    assert lead == "Risk sentiment"


def test_situations_group_currencies_and_pairs():
    started = date(2026, 1, 1)
    episodes = [
        {"situation_id": "labor_deterioration", "name": "Labor deterioration", "scope": "currency",
         "scope_key": currency, "severity": "medium", "started_at": started,
         "evidence": {"current": {"unemployment_gap_pp": .6}}}
        for currency in ("USD:US", "EUR:DE", "NZD:NZ")
    ] + [
        {"situation_id": "correlation_break", "name": "Correlation break", "scope": "pair",
         "scope_key": pair, "severity": "medium", "started_at": started,
         "evidence": {"current": {"rates_drivers_diverging": 1}}}
        for pair in ("EUR/USD", "USD/JPY")
    ]
    cards = overview._group_situations(episodes)
    assert len(cards) == 2
    labor = next(card for card in cards if card["name"] == "Labor deterioration")
    assert labor["currencies"] == ["USD", "EUR", "NZD"]
    assert labor["href"] == "/desks/USD"
    assert "0.6pp" in labor["evidence"]
    pair = next(card for card in cards if card["name"] == "Correlation break")
    assert pair["pairs"] == ["EUR/USD", "USD/JPY"]
    assert pair["href"] == "/pairs?pair=EUR/USD"


def test_news_situation_tag_matches_topic_and_currency():
    episodes = [{"situation_id": "labor_deterioration", "name": "Labor deterioration",
                 "scope_key": "NZD:NZ"}]
    assert overview._classify_news("NZD jobs weakness deepens", episodes) == {
        "currencies": ["NZD"], "tag": "Labor deterioration"}
    assert overview._classify_news("USD jobs growth rebounds", episodes)["tag"] == "Economic data"
    assert overview._classify_news("Tourism season begins", episodes) is None


def test_board_excludes_future_observations(monkeypatch):
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
    async def board(_session):
        today = datetime.now(UTC).date()
        return {"rows": [{"currency": "USD", "date": today, "overall_score": 1},
                         {"currency": "EUR", "date": today + timedelta(days=1), "overall_score": 2}]}
    monkeypatch.setattr(overview, "get_sessionmaker", lambda: lambda: Session())
    monkeypatch.setattr(overview, "get_macro_state_board", board)
    rows = __import__("asyncio").run(overview._board())["rows"]
    assert [row["currency"] for row in rows] == ["USD"]


def test_key_events_group_components_and_use_fourteen_days(monkeypatch):
    when = datetime.now(UTC) + timedelta(days=10)
    class Result:
        def mappings(self):
            return self
        def all(self):
            return [{"released_at": when, "period": "2026-09", "display_name": name,
                     "canonical_name": canonical, "country_code": "US", "currency_code": "USD"}
                    for name, canonical in (("Core CPI (YoY)", "core_cpi_yoy"),
                                            ("Headline CPI (YoY)", "cpi_headline_yoy"))]
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def execute(self, query):
            assert "interval '14 days'" in str(query)
            assert "i.importance = 1" in str(query)
            return Result()
    async def tracking():
        return {"USD": "inflation running hot vs projections"}
    monkeypatch.setattr(overview, "get_sessionmaker", lambda: lambda: Session())
    monkeypatch.setattr(overview, "_event_tracking", tracking)
    result = __import__("asyncio").run(overview.key_events())
    assert len(result["rows"]) == 1
    assert result["rows"][0]["event"] == "CPI (Sep)"
    assert result["rows"][0]["components"] == 2
    assert "Fed dual mandate" in result["rows"][0]["why"]
    assert result["rows"][0]["impact"] == "High"
