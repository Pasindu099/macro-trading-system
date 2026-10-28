"""Bounded CSV streams for read-only research services."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.db.session import get_sessionmaker

CSV_LIMIT = 50_000


def csv_response(sql: str, params: dict[str, Any], columns: Sequence[str], filename: str) -> StreamingResponse:
    """Own the connection for the stream's lifetime; never materialize the export."""
    async def chunks() -> AsyncIterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        def line(values):
            buffer.seek(0)
            buffer.truncate(0)
            # Neutralize spreadsheet formula strings; numeric values stay numeric.
            values = [json.dumps(value, default=str, ensure_ascii=True) if isinstance(value, (dict, list)) else value
                      for value in values]
            writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(
                ("=", "+", "-", "@")) else value for value in values])
            return buffer.getvalue()

        yield line(columns)
        async with get_sessionmaker()() as session:
            result = await session.stream(text(sql), {**params, "export_limit": CSV_LIMIT})
            try:
                count = 0
                async for row in result.mappings():
                    if count >= CSV_LIMIT:
                        break
                    yield line([row.get(column) for column in columns])
                    count += 1
            finally:
                await result.close()

    return StreamingResponse(chunks(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Export-Row-Limit": str(CSV_LIMIT),
        "Cache-Control": "no-store",
    })
