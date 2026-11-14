"""Read-only ASGI smoke check; authentication disabled only in this process."""
import asyncio
import logging
from types import SimpleNamespace
from time import monotonic

import httpx
import app.auth as auth
from app.main import app
from app.services import positioning_page


async def main():
    logging.basicConfig(level=logging.ERROR)
    auth.get_settings = lambda: SimpleNamespace(auth_enabled=False)
    paths = ["/", "/desks/USD", "/pairs", "/central-banks", "/calendar", "/news", "/positioning",
             "/desks/USD/panels/verdict", "/desks/USD/panels/catalysts", "/desks/USD/panels/scenarios",
             "/central-banks/panels/upcoming?bank=FED", "/central-banks/panels/speakers?bank=FED",
             "/central-banks/panels/library?bank=FED", "/data-freshness"]
    paths += [f"/positioning/panels/{panel}" for panel in positioning_page.PANELS]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path in paths:
            start = monotonic()
            response = await client.get(path)
            broken = any(marker in response.text for marker in
                         ("This panel could not load", "pending AI layer", "Phase 3, pending", "state-error"))
            print(response.status_code, f"{monotonic()-start:.2f}s", "BROKEN" if broken else "OK", path, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
