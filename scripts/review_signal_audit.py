"""Print only the USD verdict and next-meeting inputs for the dashboard review."""
import asyncio
import logging

from app.db.session import session_scope
from app.services.rate_probability import get_rate_probability_view
from app.services.verdict import get_verdict


async def main():
    logging.basicConfig(level=logging.ERROR)
    async with session_scope() as session:
        view = await get_rate_probability_view("FED", session)
    print("FED", {k: view.get(k) for k in ("current_rate", "current_rate_source", "current_rate_as_of", "market_data")})
    print("NEXT", (view.get("meetings") or [])[:1])
    verdict = await get_verdict("USD")
    print("VERDICT", {k: verdict.get(k) for k in ("status", "bias", "score", "conviction", "horizon")})
    print("COMPONENTS", {k: {p: v.get(p) for p in ("value", "contribution", "reason")} for k, v in verdict.get("why", {}).items()})


if __name__ == "__main__":
    asyncio.run(main())
