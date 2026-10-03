from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.knowledge_figures import get_figure_artifact


@pytest.mark.asyncio
async def test_path_outside_artifact_root_is_rejected():
    result = SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(
        interpretation_status="pending", artifact_path=str(Path(__file__).resolve()),
        image_format="png",
    ))
    session = AsyncMock()
    session.execute.return_value = result
    with pytest.raises(FileNotFoundError, match="path is invalid"):
        await get_figure_artifact(session, 1)
