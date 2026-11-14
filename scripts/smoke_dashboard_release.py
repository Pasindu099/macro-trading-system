"""Read-only authenticated HTTP checks; never print cookies or credentials."""
import asyncio
from time import monotonic

import httpx
from sqlalchemy import text

from app.auth import create_session_token
from app.db.session import dispose_engine, get_sessionmaker
from app.settings import get_settings


async def main():
    async with get_sessionmaker()() as session:
        user_id = (await session.execute(text(
            "SELECT id FROM users WHERE is_active ORDER BY id LIMIT 1"
        ))).scalar_one_or_none()
    assert user_id is not None, "No active account for authenticated smoke"
    cookies = {get_settings().auth_session_cookie: create_session_token(user_id)}
    paths = ["/health", "/", "/desks/USD", "/pairs", "/central-banks", "/calendar", "/news", "/positioning",
             "/desks/USD/panels/verdict", "/desks/USD/panels/catalysts", "/desks/USD/panels/scenarios",
             "/central-banks/panels/upcoming?bank=FED", "/central-banks/panels/speakers?bank=FED",
             "/central-banks/panels/library?bank=FED", "/data-freshness"]
    paths += [f"/positioning/panels/{panel}" for panel in
              ("summary", "crowding", "flows", "squeeze", "price", "extremes", "retail")]
    failures = []
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", cookies=cookies, timeout=90) as client:
        for path in paths:
            start = monotonic()
            try:
                response = await client.get(path)
                ok = response.status_code == 200 and not any(marker in response.text for marker in
                    ("This panel could not load", "pending AI layer", "Phase 3, pending", "state-error"))
                if not ok:
                    failures.append(path)
                print("PASS" if ok else "FAIL", response.status_code,
                      f"{monotonic()-start:.2f}s", path, flush=True)
            except httpx.HTTPError as exc:
                failures.append(path)
                print("FAIL", type(exc).__name__, path, flush=True)
    await dispose_engine()
    assert not failures, f"Failed checks: {', '.join(failures)}"
    print(f"Authenticated release smoke: {len(paths)} passed", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
