"""Authenticated situation, verdict, and scenario JSON endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import require_role
from app.services.scenarios import get_scenarios
from app.services.situations import get_situation_episodes
from app.services.verdict import get_verdict
from app.settings import get_settings


def _require_viewer(request: Request) -> None:
    if get_settings().auth_enabled:
        require_role("viewer")(request)


router = APIRouter(prefix="/api", tags=["desk-insights"], dependencies=[Depends(_require_viewer)])


@router.get("/situations")
async def situations(active: bool | None = Query(None)) -> dict:
    return {"episodes": await get_situation_episodes(active=active)}


@router.get("/situations/history")
async def situation_history() -> dict:
    return {"episodes": await get_situation_episodes()}


@router.get("/verdict/{currency}")
async def verdict(currency: str) -> dict:
    result = await get_verdict(currency)
    if result["status"] == "unavailable" and result.get("reason") == "No verdict for this desk":
        raise HTTPException(status_code=404, detail=result["reason"])
    return result


@router.get("/scenarios/{currency}")
async def scenarios(currency: str) -> dict:
    result = await get_scenarios(currency)
    if result["status"] == "unavailable" and result.get("reason") == "No scenarios for this desk":
        raise HTTPException(status_code=404, detail=result["reason"])
    return result
