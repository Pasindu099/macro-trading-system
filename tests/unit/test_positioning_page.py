from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.templating import Jinja2Templates

from app.services import positioning as pos, positioning_page as page

TEMPLATES = Jinja2Templates(directory=str(Path("app/web/templates")))
P = {"lookback": "3y", "ccy": "JPY", "window": "1W"}


def render(panel_id, ctx):
    return TEMPLATES.get_template("positioning/panel.html").render(
        panel_id=panel_id, ctx=ctx, p=P, titles=page.TITLES, lookbacks=page.LOOKBACK_LABELS,
        currency_items=[(c, c) for c in ("USD", "JPY")])


def weeks(nets):
    return [pos.Week(date(2020, 1, 7) + timedelta(weeks=i), max(n, 0), max(-n, 0), 100_000)
            for i, n in enumerate(nets)]


def test_pct_cell_crowded_outline_and_side():
    assert page.pct_cell(90)["crowded"] == "long" and page.pct_cell(90)["kind"] == "long"
    assert page.pct_cell(12)["label"] == "12 · crowded short"
    assert page.pct_cell(None)["label"] == "—"


def test_crowd_read_and_flow_kind_are_descriptive():
    lf = {"percentile": 10.0}
    assert page.crowd_read(lf, {"status": "high"}) == "Crowded short; shorts being cut while price moves against them"
    assert page.crowd_read({"percentile": 70.0}, None) == "Leaning long"
    assert page.flow_kind(500, -9000) == "short-covering" and page.flow_kind(-500, 9000) == "long-liquidation"


def test_desk_links_enabled_or_pending():
    assert page.desk_link("USD") == {"href": "/desks/usd", "label": "Open USD desk →"}
    assert page.desk_link("NZD") == {"href": None, "label": "NZD desk pending"}


async def test_summary_pending_without_cot(monkeypatch):
    async def summary(lookback):
        return {"summary": {"text": "x"}, "release": {"as_of": None}}

    monkeypatch.setattr(pos, "get_summary", summary)
    ctx = await page.summary("3y")
    assert ctx["state"] == "pending"
    assert "No COT report stored yet." in render("summary", ctx)


async def test_summary_renders_release_and_how_to_use(monkeypatch):
    async def summary(lookback):
        return {"summary": {"text": "Speculators crowded short JPY (12th percentile)."},
                "release": pos.release_info(date(2026, 10, 13))}

    monkeypatch.setattr(pos, "get_summary", summary)
    html = render("summary", await page.summary("3y"))
    assert "Tue 13 Oct" in html and "Fri 16 Oct" in html and "How to use it" in html
    assert "expected" not in html.lower() and "likely" not in html.lower()


async def test_extremes_hide_stats_below_five_episodes(monkeypatch):
    bands = {b: {"episodes": n, "avg_move_4w_pct": 0.5, "avg_move_8w_pct": -1.0, "against_crowd_after_8w": 60.0,
                 "max_adverse_move_8w": 2.0} for b, n in zip(pos.EXTREME_BANDS, (7, 3, 5, 0))}

    async def extremes(ccy):
        return {"currency": ccy, "bands": bands}

    async def positions(currencies=None):
        return {"JPY": {"leveraged_funds": weeks([-50_000 + i * 10 for i in range(200)])}}

    monkeypatch.setattr(pos, "get_extremes", extremes)
    monkeypatch.setattr(pos, "load_positions", positions)
    ctx = await page.extremes("3y", ccy="JPY")
    assert [r["enough"] for r in ctx["rows"]] == [True, False, True, False]
    html = render("extremes", ctx)
    assert html.count("too few episodes") == 2 and "Past episodes, not a forecast" in html
    assert 'class="is-current"' in html  # latest net is the top of its window -> above 90th band


async def test_retail_not_configured_message(monkeypatch):
    async def ivr(lookback):
        return {"retail_state": "not_configured", "retail_message": "Myfxbook credentials not configured",
                "pairs": [{"pair": "EUR/USD", "cot_implied": -69.0, "cot_side": "short-biased",
                           "retail_long_pct": None, "retail_taken_at": None, "signal": "no signal"}]}

    monkeypatch.setattr(pos, "get_institutions_vs_retail", ivr)
    html = render("retail", await page.retail("3y"))
    assert html.count("Myfxbook credentials not configured") == 1
    assert "Short-biased (-69)" in html and 'href="/pairs"' in html


def test_sparkline_needs_seven_days():
    assert page.sparkline([{"long_pct": 40}] * 6) is None
    assert page.sparkline([{"long_pct": 40 + i} for i in range(7)]).count(",") == 7


def test_chart_geometry_and_divergence_spans():
    history = [{"date": date(2026, 1, 6) + timedelta(weeks=i), "net": n} for i, n in enumerate([-10, 20, 30, -5])]
    geo = page.chart(history, [100, 101, None, 99], [False, True, True, False])
    assert geo["long"].count("M") == 2 and geo["short"].count("M") == 2
    assert geo["spans"] == [{"x": 150.0, "w": 300.0}] and geo["price"].count(",") == 3


@pytest.mark.parametrize("query,status", [("", 200), ("?ccy=xxx", 400), ("?lookback=2y", 400)])
def test_route_params(query, status):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import positioning_page as route

    from fastapi.staticfiles import StaticFiles

    app = FastAPI()
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
    app.include_router(route.router)
    assert TestClient(app).get("/positioning" + query).status_code == status


def test_css_reserves_green_red_for_price():
    css = Path("app/web/static/css/positioning.css").read_text(encoding="utf-8")
    assert "--price-up" in css and "--price-down" in css
    for rule in (".pos-long", ".pos-short", ".bar-long", ".bar-short", ".pct-long", ".pct-short"):
        line = next(l for l in css.splitlines() if l.startswith(rule))
        assert "price-" not in line


def test_desk_and_overview_link_to_positioning_with_currency():
    desk_html = TEMPLATES.get_template("desk/panels/positioning.html").render(
        ctx={"state": "ok", "rows": []}, desk={"currency": "EUR"})
    assert 'href="/positioning?ccy=EUR#pos-price"' in desk_html
    row = {"currency": "USD", "enabled": True, "score": None, "bias": None, "partial": False,
           "cb_regime": None, "gap": "", "cot": 88.0, "cot_label": "crowded long", "thesis": None}
    overview_html = TEMPLATES.get_template("overview/panel.html").render(
        panel_id="ranking", ctx={"state": "ok", "rows": [row]})
    assert 'href="/positioning?ccy=USD#pos-price"' in overview_html
