"""Run the outlier flagger and record counts in ingestion_runs."""

import asyncio

from app.services.rates_quality import run_rates_outlier_job


if __name__ == "__main__":
    print(asyncio.run(run_rates_outlier_job()))
