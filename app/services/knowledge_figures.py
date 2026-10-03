"""Resolve a knowledge figure only within the controlled artifact root."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeFigure


async def get_figure_artifact(session: AsyncSession, figure_id: int) -> tuple[Path, str]:
    figure = (await session.execute(
        select(KnowledgeFigure).where(KnowledgeFigure.id == figure_id)
    )).scalar_one_or_none()
    if figure is None or figure.interpretation_status.startswith("ignored"):
        raise FileNotFoundError("Figure not found")
    artifact_root = Path("data/knowledge_bank/visuals").resolve()
    artifact_path = Path(figure.artifact_path).resolve()
    try:
        artifact_path.relative_to(artifact_root)
    except ValueError:
        raise FileNotFoundError("Figure artifact path is invalid") from None
    if not artifact_path.exists() or not artifact_path.is_file():
        raise FileNotFoundError("Figure artifact file not found")
    media_type = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
    }.get((figure.image_format or "").lower(), "application/octet-stream")
    return artifact_path, media_type
