"""Print the own engine's next 3 Fed meetings (Step 7 Part D before/after comparison).

Run: docker compose exec app python -m scripts.step7_fed_probabilities
"""

import asyncio
import json

from app.db.session import session_scope
from app.services.rate_probability import get_rate_probability_view


async def main() -> None:
    async with session_scope() as session:
        view = await get_rate_probability_view("FED", session)
    rows = [{
        "meeting": str(m["meeting_at"])[:10], "cut": m["cut_prob"], "hold": m["hold_prob"], "hike": m["hike_prob"],
        "implied": round(m["implied_rate"], 4) if m["implied_rate"] is not None else None, "state": m["data_state"],
    } for m in view["meetings"][:3]]
    print(json.dumps({"current_rate": view["current_rate"], "source": view["market_data"].get("source"),
                      "curve_date": str(view["market_data"].get("curve_date")), "meetings": rows}))


if __name__ == "__main__":
    asyncio.run(main())
