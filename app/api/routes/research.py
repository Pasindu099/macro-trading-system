"""Research Lab JSON endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.correlation_service import (
    get_correlation_matrix,
    get_correlation_series,
)

router = APIRouter(prefix="/api/correlation", tags=["correlation"])
SessionDep = Depends(get_session)


@router.get("/series")
async def correlation_series(
    pair: str = Query(..., examples=["USDJPY"]),
    tf: str = Query("3M", examples=["3M", "6M", "1Y", "2Y"]),
    overlays: str | None = Query("yieldDiff,cpi,rate,pmi"),
    session: AsyncSession = SessionDep,
) -> dict:
    try:
        return await get_correlation_series(
            session,
            pair=pair,
            tf=tf,
            overlays=overlays,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/matrix")
async def correlation_matrix(
    pair: str = Query(..., examples=["USDJPY"]),
    session: AsyncSession = SessionDep,
) -> dict:
    try:
        return await get_correlation_matrix(session, pair=pair)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
