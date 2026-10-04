"""Central-bank projections, tracking, regime and gap."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import require_role
from app.services import cb_tracking, ecb_projections, ecb_regime, ecb_tracking, fed_projections, fed_regime
from app.settings import get_settings

SUPPORTED = {"FED", "ECB"}


def _require_viewer(request: Request) -> None:
    if get_settings().auth_enabled:
        require_role("viewer")(request)


router = APIRouter(prefix="/api/cb", tags=["central-banks"], dependencies=[Depends(_require_viewer)])


def _bank(bank: str) -> str:
    code = bank.upper()
    if code not in SUPPORTED:
        raise HTTPException(status_code=404, detail=f"Projections for {code} are not available")
    return code


def _round_date(round_: str | None) -> date | None:
    if round_ in (None, "latest"):
        return None
    try:
        return date.fromisoformat(round_)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="round must be 'latest' or YYYY-MM-DD") from exc


@router.get("/{bank}/projections")
async def projections(bank: str, round: str = Query("latest", pattern=r"^(latest|all|\d{4}-\d{2}-\d{2})$")) -> dict:
    return await (ecb_projections.get_projections(round) if _bank(bank) == "ECB"
                  else fed_projections.get_projections(round))


@router.get("/{bank}/dots")
async def dots(bank: str, round: str = Query("latest")) -> dict:
    if _bank(bank) == "ECB":
        return {"bank": "ECB", "status": "unavailable", "reason": "ECB publishes point projections, not rate dots"}
    return await fed_projections.get_dots(_round_date(round))


@router.get("/{bank}/risk-balance")
async def risk_balance(bank: str, round: str = Query("latest")) -> dict:
    if _bank(bank) == "ECB":
        return {"bank": "ECB", "status": "unavailable", "reason": "ECB MPD has no Fed-style risk-balance counts"}
    return await fed_projections.get_risk_balance(_round_date(round))


@router.get("/{bank}/tracking")
async def tracking(bank: str) -> dict:
    if _bank(bank) == "ECB":
        return {**await ecb_tracking.get_tracking(), "revisions": await ecb_tracking.get_revisions()}
    return {**await cb_tracking.get_tracking(), "revisions": await cb_tracking.get_revisions()}


@router.get("/{bank}/regime")
async def regime(bank: str) -> dict:
    if _bank(bank) == "ECB":
        return await ecb_regime.get_regime()
    return await fed_regime.get_regime()


@router.get("/{bank}/gap")
async def gap(bank: str) -> dict:
    if _bank(bank) == "ECB":
        return await ecb_regime.get_gap()
    return await fed_regime.get_gap()
