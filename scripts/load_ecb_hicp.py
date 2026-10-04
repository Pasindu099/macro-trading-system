"""Load ECB seasonally adjusted HICP index levels for tracking."""

import asyncio

from app.services.ecb_series import HICP_SA, ingest_series


if __name__ == "__main__":
    print({"series": HICP_SA, "rows": asyncio.run(ingest_series(HICP_SA))})
