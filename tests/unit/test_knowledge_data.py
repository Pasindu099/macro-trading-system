from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from app.services.knowledge_data import get_knowledge_document_detail


@pytest.mark.asyncio
async def test_missing_knowledge_document_stops_before_child_queries():
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    assert await get_knowledge_document_detail(session, 99) is None
    session.execute.assert_awaited_once()
