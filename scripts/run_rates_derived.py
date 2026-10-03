"""Run and log the outlier-then-spread chain on demand."""

import asyncio

from app.services.rates_derived_jobs import run_rates_derived


if __name__ == "__main__":
    print(asyncio.run(run_rates_derived()))
