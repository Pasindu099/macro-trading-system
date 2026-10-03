from pathlib import Path

import pytest

from app.services import bank_research_admin


def test_bank_research_folder_validation_and_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bank_research_admin, "STATE_PATH", tmp_path / "admin_state.json")
    with pytest.raises(ValueError, match="Paste a Google Drive"):
        bank_research_admin.save_folder_url("  ")
    with pytest.raises(ValueError, match="Could not parse"):
        bank_research_admin.save_folder_url("https://example.com/invalid")
    assert not bank_research_admin.STATE_PATH.exists()

    saved = bank_research_admin.save_folder_url(" https://drive.google.com/drive/folders/abc_123 ")
    assert saved["folder_url"] == "https://drive.google.com/drive/folders/abc_123"
    assert saved["status"] == "saved"
    queued = bank_research_admin.queue_refresh()
    assert queued["folder_url"] == saved["folder_url"]
    assert queued["status"] == "queued"
