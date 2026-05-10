# Macro Dashboard — Project Context

A real-time macroeconomic intelligence platform built solo for G10 FX traders and analysts.

## What it does
Tracks 436+ economic indicators across 10 G10 countries (US, EU, Germany, France, UK, Japan,
Australia, New Zealand, Canada, Switzerland). Surfaces live economic calendar data, macro
surprises (actual vs forecast), currency strength rankings, 10Y yield benchmarks, and central
bank research summaries — all in one dashboard, updated 3× daily.

## Tech stack
- **Backend:** Python 3.11, FastAPI, async PostgreSQL (SQLAlchemy + asyncpg), Alembic
- **Data pipelines:** async httpx, APScheduler (3× daily ingestion jobs), pandas
- **Frontend:** Jinja2 server-rendered templates, Plotly charts, vanilla JS
- **AI layer:** OpenAI gpt-4o-mini for bank research PDF summarisation
- **Data source:** EODHD economic calendar API
- **Deployment:** Docker Compose, uvicorn workers

## Key engineering work done so far
- Config-driven indicator canonicalization: YAML-based mapping of raw EODHD event strings to
  canonical indicator names with alias support — no redeployment needed to add new mappings
- Historical backfill pipeline with checkpoint/resume (44k+ releases, 77 indicators loaded)
- Google Drive → PDF parse → OpenAI summarise → 7-day TTL cache pipeline for daily bank research
- Currency strength scoring engine with 1M/3M/6M lookback windows
- 10Y yield benchmark tracking for 8 currencies (USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD)
- Admin panel for unmapped event monitoring and ingestion run logs
- Macro reporting tools: brief builder, event prep matrices, bond yield differential panels
- Graceful APScheduler shutdown with SIGTERM drain hooks for clean Docker container exits

## Scale
- 436+ raw event types canonicalized across 10 countries
- 3× daily automated ingestion + on-demand backfill
- Full history from 2020 loaded into PostgreSQL
- 8-category indicator taxonomy: Inflation, Growth, Labor, Monetary Policy, Trade, Sentiment,
  Housing, Other — with importance tiers 1–3

## Author context
Solo developer. Looking for roles in fintech, data engineering, or backend engineering.
Building this to demonstrate production-quality system design, data pipeline engineering,
and domain knowledge in macro economics and FX markets.
