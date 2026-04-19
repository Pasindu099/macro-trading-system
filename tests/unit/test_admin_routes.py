from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.routes.admin import _build_unmapped_where, admin_unmapped_events


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _RowsResult:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, results: list[object]) -> None:
        self._results = results
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return self._results.pop(0)


def test_build_unmapped_where_without_country() -> None:
    where_sql, params = _build_unmapped_where(None)

    assert where_sql == "indicator_id IS NULL"
    assert params == {}


def test_build_unmapped_where_normalizes_country() -> None:
    where_sql, params = _build_unmapped_where("us")

    assert where_sql == "indicator_id IS NULL AND raw_payload->>'country' = :country"
    assert params == {"country": "US"}


@pytest.mark.asyncio
async def test_admin_unmapped_events_totals_respect_country_filter() -> None:
    session = _FakeSession([
        _ScalarResult(4),
        _ScalarResult(2),
        _RowsResult([
            SimpleNamespace(
                country="US",
                event_type="Some Indicator",
                comparison="yoy",
                release_count=3,
                latest_release=datetime(2026, 4, 18, tzinfo=timezone.utc),
            )
        ]),
    ])

    response = await admin_unmapped_events(
        session=session,
        country="us",
        limit=10,
        min_count=2,
    )

    assert response.data.total_unmapped_releases == 4
    assert response.data.total_distinct_types == 2
    assert len(response.data.groups) == 1
    assert response.data.groups[0].country == "US"
    assert all(":country" in statement for statement in session.statements)

