"""Load the ECB daily broad nominal EUR effective exchange-rate series."""

import asyncio

from app.services.ecb_series import EUR_EER_BROAD_DAILY, ingest_series


if __name__ == "__main__":
    print({"series": EUR_EER_BROAD_DAILY, "rows": asyncio.run(ingest_series(EUR_EER_BROAD_DAILY))})
