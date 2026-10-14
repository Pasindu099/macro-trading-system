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


def test_form_parsing_keeps_trade_fields_optional():
    form = FormData([("event_title", " CPI surprise "), ("event_date", "2026-10-01"), ("currency", "usd"),
                     ("pairs", "EUR/USD"), ("pairs", "XXX/YYY"), ("entry_price", ""), ("pnl_pips", "12.5"),
                     ("direction", "long"), ("ai_note_raw", "ignored")])
    data = parse_entry_form(form)
    assert data["event_title"] == "CPI surprise" and data["currency"] == "USD"
    assert data["pairs"] == ["EUR/USD"]
    assert data["entry_price"] is None and data["pnl_pips"] == Decimal("12.5")
    assert data["traded"] is False and data["direction"] is None  # untraded entries carry no direction
    assert "ai_note_raw" not in data
    with pytest.raises(ValueError):
        parse_entry_form(FormData([("size", "lots")]))


@pytest.mark.asyncio
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
