"""Knowledge Bank non-page resources."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.knowledge_figures import get_figure_artifact

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/figures/{figure_id}/image")
async def knowledge_figure_image(
    figure_id: int, session: AsyncSession = Depends(get_session),
) -> FileResponse:
    try:
        artifact_path, media_type = await get_figure_artifact(session, figure_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(artifact_path, media_type=media_type)
