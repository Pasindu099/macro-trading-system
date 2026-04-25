# Production Integration Plan

## Deployment Steps

1. Add `scripts.build_production_currency_strength` to the scheduled data pipeline after macro ingestion and EDA preprocessing.
2. Store the latest output table `production_currency_strength_signals.csv` or its database equivalent for the dashboard API.
3. Expose latest rows from `latest_currency_strength_signals.csv` in the frontend currency strength panel.
4. Keep the existing currency stance layer visible during an observation period and label the new model as `research_candidate` until metrics stabilize.

## Infrastructure Requirements

- Python dependencies already present in the project: pandas, numpy, plotly.
- Existing Docker app container and Postgres database.
- Scheduled execution after successful data ingestion.
- Persistent storage for generated reports and validation metrics.

## DevOps Checklist

- Add pipeline step health checks.
- Alert on missing latest signals, low indicator coverage, or stale source releases.
- Archive model output versions with timestamped artifacts.
- Add dashboard API endpoint only after acceptance of validation metrics.
