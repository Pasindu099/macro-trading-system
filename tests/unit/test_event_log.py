from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import FormData

from app import auth
from app.api.routes.event_log import parse_entry_form
from app.db.models import EventReactionNote
from app.db.session import get_session
from app.main import app
from app.services import event_reaction_log as log


class FakeSession:
    def __init__(self, *notes):
        self.notes = {note.id: note for note in notes}
        self.deleted = []

    def add(self, note):
        note.id = note.id or max(self.notes, default=0) + 1
        self.notes[note.id] = note

    async def get(self, _model, key):
        return self.notes.get(key)

    async def flush(self):
        pass

    async def delete(self, note):
        self.deleted.append(note.id)
        self.notes.pop(note.id)


def _note(**values):
    base = dict(id=1, indicator_release_id=None, event_title="ECB presser", event_date=date(2026, 10, 1),
                currency="EUR", ai_note_raw=None, ai_note_final=None, traded=False, pairs=[],
                actual_value=None, forecast_value=None, previous_value=None,
                expectation_before=None, outcome_after=None)
    return EventReactionNote(**(base | values))


def test_form_parsing_moves_per_instrument_and_ignores_trade_fields():
    form = FormData([("event_title", " CPI surprise "), ("event_date", "2026-10-01"), ("currency", "usd"),
                     ("pairs", "EUR/USD"), ("pairs", "XAU/USD"), ("pairs", "US100"), ("pairs", "XXX/YYY"),
                     ("move__EUR/USD__1m", "-12.5"), ("move__EUR/USD__1h", "+30"), ("move__EUR/USD__4h", ""),
                     ("move__XAU/USD__5m", "8.25"), ("move__US100__30m", "-145"),
                     ("move__GBP/USD__1m", "9"),          # instrument not ticked: ignored
                     ("pnl_pips", "12.5"), ("direction", "long"), ("traded", "yes"), ("ai_note_raw", "ignored")])
    data = parse_entry_form(form)
    assert data["event_title"] == "CPI surprise" and data["currency"] == "USD"
    assert data["pairs"] == ["EUR/USD", "XAU/USD", "US100"]
    assert data["moves"] == {"EUR/USD": {"1m": Decimal("-12.5"), "1h": Decimal("30.0")},
                             "XAU/USD": {"5m": Decimal("8.25")}, "US100": {"30m": Decimal("-145.0")}}
    assert not {"traded", "direction", "pnl_pips", "ai_note_raw"} & set(data)   # trades are not edited
    with pytest.raises(ValueError, match="EUR/USD 5m move must be a number"):
        parse_entry_form(FormData([("pairs", "EUR/USD"), ("move__EUR/USD__5m", "lots")]))


def test_form_lists_gold_and_indices_with_all_horizons():
    from app.api.routes.event_log import INSTRUMENTS, templates
    from app.services.event_reaction_log import MOVE_HORIZONS
    assert {"XAU/USD", "US100", "US30", "US500"} <= set(INSTRUMENTS) and MOVE_HORIZONS == ("1m", "5m", "30m", "1h", "4h")
    assert templates.env.get_template("calendar/event_log_form.html")      # template compiles


async def test_create_edit_delete_free_text_entry():
    session = FakeSession()
    note = await log.save_entry(session, None, {"event_title": "Tariff headline", "event_date": date(2026, 10, 2),
                                                "currency": "CNY", "traded": False})
    assert note.id == 1 and note.event_title == "Tariff headline" and note.entry_price is None
    await log.save_entry(session, 1, {"traded": True, "direction": "short", "pnl_pips": Decimal("-8")})
    assert note.traded is True and note.direction == "short" and note.event_title == "Tariff headline"
    with pytest.raises(ValueError):
        await log.save_entry(session, 1, {"direction": "sideways"})
    with pytest.raises(ValueError):
        await log.save_entry(FakeSession(), None, {"event_title": None})
    assert await log.delete_entry(session, 1) is True and session.deleted == [1]
    assert await log.delete_entry(session, 1) is False


@pytest.mark.asyncio
async def test_editing_changes_final_note_but_never_raw():
    note = _note(ai_note_raw="Model text", ai_note_final="Model text")
    session = FakeSession(note)
    await log.save_entry(session, 1, {"ai_note_final": "My edit", "ai_note_raw": "tamper"})
    assert note.ai_note_final == "My edit" and note.ai_note_raw == "Model text"
    note.ai_interpretation = "Admin edit"  # admin API field maps to the final note
    assert note.ai_note_final == "Admin edit" and note.ai_note_raw == "Model text"


@pytest.mark.asyncio
async def test_ai_note_generation_success_failure_and_no_overwrite(monkeypatch):
    note = _note()
    session = FakeSession(note)

    async def fail(_prompt):
        raise log.AINoteError("OpenAI returned 429: no credits")
    monkeypatch.setattr(log, "_call_openai_interpretation", fail)
    with pytest.raises(log.AINoteError, match="429"):
        await log.generate_ai_interpretation(session, 1)
    assert note.ai_note_raw is None and note.ai_note_final is None

    async def succeed(prompt):
        assert "ECB presser" in prompt
        return "Hawkish read."
    monkeypatch.setattr(log, "_call_openai_interpretation", succeed)
    await log.generate_ai_interpretation(session, 1)
    assert note.ai_note_raw == "Hawkish read." and note.ai_note_final == "Hawkish read."
    with pytest.raises(log.AINoteError):
        await log.generate_ai_interpretation(session, 1)
    assert note.ai_note_raw == "Hawkish read."


@pytest.mark.asyncio
async def test_missing_openai_key_is_reported(monkeypatch):
    monkeypatch.setattr(log, "get_settings", lambda: SimpleNamespace(openai_api_key=None, openai_model="x"))
    with pytest.raises(log.AINoteError, match="OPENAI_API_KEY"):
        await log._call_openai_interpretation("prompt")


def _client(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(auth_enabled=False))
    return TestClient(app)


def test_delete_requires_confirmation(monkeypatch):
    response = _client(monkeypatch).post("/calendar/event-log/1/delete", data={})
    assert response.status_code == 400


def test_admin_api_ai_failure_still_returns_502(monkeypatch):
    async def fail(_session, _note_id):
        raise log.AINoteError("no credits")

    async def session_override():
        yield FakeSession()
    monkeypatch.setattr(log, "generate_ai_interpretation", fail)
    app.dependency_overrides[get_session] = session_override
    try:
        response = _client(monkeypatch).post("/api/admin/event-log/notes/1/ai")
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert response.status_code == 502
    assert response.json()["detail"] == "AI interpretation generation failed."


def test_admin_note_shape_unchanged():
    from app.api.routes.admin import _note_to_dict
    payload = _note_to_dict(_note(indicator_release_id=7, ai_note_raw="raw", ai_note_final="final"))
    assert set(payload) == {"id", "indicator_release_id", "forecast_value", "actual_value",
                            "previous_value", "manual_notes", "ai_interpretation", "ai_generated_at"}
    assert payload["ai_interpretation"] == "final"


def test_move_averages_by_event_instrument_horizon_and_surprise():
    from app.services.event_reaction_log import aggregate_moves, surprise_group
    assert (surprise_group(Decimal("3.1"), Decimal("3.0")), surprise_group(Decimal("2.9"), Decimal("3.0")),
            surprise_group(None, Decimal("3.0"))) == ("above", "below", None)
    rows = [{"key": "i:7", "event": "CPI", "currency": "USD", "note_id": n, "actual": a, "forecast": Decimal("3.0"),
             "instrument": "EUR/USD", "horizon": "1h", "move": Decimal(m)}
            for n, a, m in ((1, Decimal("3.2"), "-20"), (2, Decimal("3.3"), "-30"), (3, Decimal("2.8"), "15"))]
    rows.append({"key": "i:7", "event": "CPI", "currency": "USD", "note_id": 3, "actual": Decimal("2.8"),
                 "forecast": Decimal("3.0"), "instrument": "XAU/USD", "horizon": "5m", "move": Decimal("4.5")})
    event = aggregate_moves(rows)["events"][0]
    cell = event["instruments"]["EUR/USD"]
    assert event["entries"] == 3 and event["indicative"] is True
    assert cell["all"]["1h"] == {"n": 3, "mean": -11.7, "mean_abs": 21.7, "min": -30.0, "max": 15.0}
    assert cell["above"]["1h"]["mean"] == -25.0 and cell["below"]["1h"]["n"] == 1
    assert event["instruments"]["XAU/USD"]["all"]["5m"]["mean"] == 4.5


def test_form_template_markup_is_balanced_and_has_no_default_instruments():
    from pathlib import Path
    from app.api.routes import event_log as routes
    source = Path("app/web/templates/calendar/event_log_form.html").read_text(encoding="utf-8")
    assert source.count("<fieldset") == source.count("</fieldset>") and "\niv>" not in source
    assert "default_pairs" not in Path(routes.__file__).read_text(encoding="utf-8")


def test_intraday_move_preserves_entered_precision():
    from app.api.routes.event_log import parse_moves

    class Form:
        def get(self, key):
            return "-1.234567" if key == "move__EUR/USD__1m" else ""

    assert parse_moves(Form(), ["EUR/USD"])["EUR/USD"]["1m"] == Decimal("-1.234567")
