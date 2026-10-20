"""Step 12 Part G: Central Bank Terminal panels render data / pending / error states."""

from datetime import date

from starlette.requests import Request

from app.api.routes import cb_terminal as routes
from app.services import cb_terminal, desks

TEMPLATE = routes.templates.get_template("cb/panel.html")


def render(panel_id, ctx, bank="FED"):
    return TEMPLATE.render(panel_id=panel_id, bank=bank, ctx=ctx, titles=cb_terminal.TITLES)


def test_pending_and_unavailable_states_show_message():
    html = render("statement", {"state": "pending", "message": "Manual import source (blocked)"}, "RBNZ")
    assert "state-pending" in html and "Manual import source" in html
    assert "state-unavailable" in render("proj-dots", {"state": "unavailable", "message": "Only the Fed"}, "ECB")


def test_speaker_table_hides_tone_when_gate_closed_and_shows_focus_mix():
    speaker = {"speaker_id": "waller", "name": "Christopher J. Waller", "role": "Governor", "voter": True,
               "last_seen": date(2026, 10, 8), "speeches_30d": 3, "comments_30d": 1,
               "focus_mix": {"inflation": 0.6, "labor": 0.4}}
    data = {"speakers": [speaker], "tone_method": None, "tone_status": "pending", "label": "Tone: pending AI layer",
            "tone_gate": {"status": "awaiting labels"}}
    html = render("speakers", {"state": "ok", "data": data, "focus": cb_terminal.FOCUS})
    assert "Tone: pending AI layer" in html and "Tone 30d" not in html and 'class="f-inflation"' in html
    shown = {**data, "tone_method": "lexicon_v2", "tone_status": "available", "label": "word-list tone, v2"}
    html = render("speakers", {"state": "ok", "data": {**shown, "speakers": [
        {**speaker, "lean": "hawkish", "tone_30d": 0.8, "shift": 0.45, "shifting": True}]}, "focus": cb_terminal.FOCUS})
    assert "word-list tone, v2" in html and "lean-hawkish" in html and "+0.80" in html and "shifting" in html


def test_library_marks_phase_3_pending():
    html = render("library", {"state": "ok", "speeches": [], "manual": None})
    assert "Phase 3, pending" in html and "No speeches stored" in html


def test_formats_cover_all_banks_and_flag_manual_imports():
    import asyncio
    ctx = asyncio.run(cb_terminal.proj_formats("BOE"))
    assert [r["bank"] for r in ctx["rows"]] == list(cb_terminal.BANKS)
    assert {r["bank"] for r in ctx["rows"] if r["manual"]} == {"RBA", "RBNZ"}
    assert next(r for r in ctx["rows"] if r["bank"] == "BOE")["current"] is True


async def test_route_error_state_and_no_cache_on_failure(monkeypatch):
    async def boom(bank, **_):
        raise RuntimeError("db down")

    async def no_freshness(session):
        return {}
    monkeypatch.setitem(cb_terminal.PANELS, "upcoming", boom)
    monkeypatch.setattr(routes.freshness, "get_data_freshness", no_freshness)
    monkeypatch.setattr(desks, "cache_get", lambda key: None)
    stored = []
    monkeypatch.setattr(desks, "cache_set", lambda key, html, ttl: stored.append(key))
    from app.main import app
    request = Request({"type": "http", "method": "GET", "path": "/central-banks/panels/upcoming", "headers": [],
                       "query_string": b"bank=SNB", "app": app, "router": app.router})
    html = (await routes.central_banks_panel(request, "upcoming")).body.decode()
    assert "state-error" in html and stored == []
