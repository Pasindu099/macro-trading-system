from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.rate_probability import get_market_data_status


@pytest.mark.asyncio
async def test_rate_market_source_status_preserves_proxy_flag():
    result = SimpleNamespace(first=lambda: SimpleNamespace(
        source="ecb_yc_proxy", curve_date="2026-10-03", rows=12,
    ))
    session = AsyncMock()
    session.execute.return_value = result
    status = await get_market_data_status("ecb", session)
    assert status == {
        "available": True, "source": "ecb_yc_proxy",
        "curve_date": "2026-10-03", "rows": 12, "is_proxy": True,
    }
