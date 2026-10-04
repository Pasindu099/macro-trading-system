"""Backfill ECB MPD rounds since 2020 (idempotent)."""

import asyncio

from app.services.ecb_projections import load_ecb_projections


if __name__ == "__main__":
    result = asyncio.run(load_ecb_projections())
    print({"rounds_loaded": len(result["loaded"]), "rows": sum(result["loaded"].values()),
           "unavailable": result["unavailable"]})
