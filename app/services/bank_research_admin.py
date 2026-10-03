"""Bank research source configuration and refresh state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.processing.bank_research import (
    BANK_RESEARCH_DIR, BankResearchConfig, build_bank_research_cache,
    load_bank_research_index, parse_drive_folder_id,
)
from app.settings import get_settings

STATE_PATH = BANK_RESEARCH_DIR / "admin_state.json"


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def get_state() -> dict[str, Any]:
    settings = get_settings()
    state = read_state()
    research = load_bank_research_index()
    return {
        "folder_url": state.get("folder_url") or research.get("folder_url")
        or settings.bank_research_drive_folder_url or "",
        "state": state,
        "has_google_drive_key": bool(settings.google_drive_api_key),
        "has_openai_key": bool(settings.openai_api_key),
        "openai_model": settings.openai_model,
        "retention_days": settings.bank_research_retention_days,
    }


def save_folder_url(folder_url: str) -> dict[str, Any]:
    normalized = folder_url.strip()
    if not normalized:
        raise ValueError("Paste a Google Drive folder URL before saving.")
    parse_drive_folder_id(normalized)
    state = {
        **read_state(), "folder_url": normalized, "status": "saved",
        "message": "Drive folder link saved.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_state(state)
    return state


def queue_refresh(folder_url: str | None = None) -> dict[str, Any]:
    candidate = folder_url or get_state()["folder_url"]
    normalized = str(candidate).strip()
    if not normalized:
        raise ValueError("Paste a Google Drive folder URL before saving.")
    parse_drive_folder_id(normalized)
    state = {
        **read_state(), "folder_url": normalized, "status": "queued",
        "message": "Refresh queued from research page.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_state(state)
    return state


async def refresh(folder_url: str) -> None:
    settings = get_settings()
    state = {
        **read_state(), "folder_url": folder_url, "status": "running",
        "message": "Refreshing bank research reports from Google Drive.",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "errors": [],
    }
    write_state(state)
    try:
        if not settings.google_drive_api_key:
            raise ValueError("GOOGLE_DRIVE_API_KEY is not configured.")
        config = BankResearchConfig(
            folder_url=folder_url, google_drive_api_key=settings.google_drive_api_key,
            openai_api_key=settings.openai_api_key, openai_model=settings.openai_model,
            retention_days=settings.bank_research_retention_days,
        )
        result = await build_bank_research_cache(config)
        state.update({
            "status": "success",
            "message": f"Refresh complete: {len(result.get('reports', []))} reports cached.",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "reports_count": len(result.get("reports", [])),
            "errors": result.get("errors", []),
        })
    except Exception as exc:
        state.update({
            "status": "failed", "message": f"Refresh failed: {exc}",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "errors": [str(exc)],
        })
    write_state(state)
