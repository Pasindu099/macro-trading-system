# Macro Dashboard — Project Context

## Overview

Macro Dashboard is a macroeconomic intelligence platform built for FX (Foreign Exchange) traders and analysts. It ingests economic data releases from multiple sources, canonicalizes and stores them in a PostgreSQL database, runs analytical pipelines to compute currency strength and central bank stance scores, and presents the intelligence through an interactive web dashboard with AI-powered news analysis.

**Stack:** FastAPI + PostgreSQL + asyncpg + APScheduler + Chart.js + Jinja2

---

## Architecture

```
EODHD API → Canonicalizer → IngestService → PostgreSQL
                                               ↓
                              Processing Pipelines (batch)
                                               ↓
                      FastAPI Routes → Jinja2 Templates → Browser
```

**Key layers:**
- `app/ingestion/` — Scheduled data fetching and canonicalization
- `app/processing/` — Batch analytical pipelines (currency strength, CB scoring, COT, bank research)
- `app/api/routes/` — HTTP endpoints for pages and JSON APIs
- `app/web/` — Templates and static assets (JS, CSS)
- `app/db/` — SQLAlchemy ORM models and session management
- `config/` — YAML configuration (countries, indicator mappings, release schedule)

---

## Data Ingestion

### Scheduler
APScheduler fires 3 daily runs targeting FX trading sessions:
- **00:00 UTC** — Asia (JP, AU, NZ)
- **09:00 UTC** — Europe/London (EU, UK, CH)
- **14:00 UTC** — Americas (US, CA)

Post-release triggers also fire ~15 minutes after major releases (NFP, CPI, etc.) using times defined in `config/release_schedule.yaml`.

### EODHD Client (`app/ingestion/eodhd_client.py`)
Fetches economic calendar events from the EODHD API for 8 tracked countries. Tracks API call usage for quota management. Each raw event contains: type, country, period, actual, estimate, previous, change%, and surprise.

### Canonicalizer (`app/ingestion/canonicalizer.py`)
Maps raw EODHD event types → stable canonical indicator names using `config/indicator_mapping.yaml` (4,546-line mapping file). Unmapped events are stored with `indicator_id=NULL` for later retroactive mapping.

### IngestService (`app/ingestion/ingest_service.py`)
Writes canonicalized events to the database. Handles revision tracking: when an indicator for a given period already exists, the old row gets `is_latest=False` and a new row is inserted. Audit logs are written to `ingestion_runs`.

---

## Database Models (`app/db/models.py`)

| Model | Table | Purpose |
|---|---|---|
| `Country` | `countries` | 8 tracked countries (US, EU, UK, JP, AU, NZ, CA, CH) with CB metadata |
| `Indicator` | `indicators` | Canonical indicator definitions with category, frequency, importance, currency effect |
| `IndicatorRelease` | `indicator_releases` | Versioned release history; `is_latest` flag; `surprise` generated column |
| `IngestionRun` | `ingestion_runs` | Audit log per scheduled or triggered run |
| `User` | `users` | Dashboard authentication with Argon2 password hashing |

**Processed schema** (batch-generated):
- `processed.currency_stance` — inflation/labor/growth stance scores per currency and window
- `processed.macro_indices` — correlation-based macro strength indices
- `processed.cb_preferred_score` — CB mandate-weighted strength scores
- `processed.cb_preferred_rankings` — relative currency rankings

---

## Processing Pipelines (`app/processing/`)

| Module | Purpose |
|---|---|
| `currency_stance.py` | Computes inflation/labor/growth stance scores per currency |
| `cb_preferred_score.py` | Central bank mandate-weighted strength scoring using CB-curated indicators |
| `cb_reaction_score.py` | Scores likely CB reaction to macro prints |
| `macro_indices.py` | Correlation-based macro strength indices |
| `macro_features.py` | Feature engineering for modeling |
| `production_currency_strength.py` | Production-grade currency strength model |
| `policy_signals.py` | Extracts policy shift signals from releases |
| `cot.py` | CFTC COT data fetching, caching, and normalization |
| `bank_research.py` | Google Drive integration + OpenAI report summarization |
| `validation.py` / `fx_validation.py` | Data quality checks |

---

## Pages & Routes

### HTML Pages (`app/api/routes/pages.py`)

| Route | Template | Description |
|---|---|---|
| `/` | `landing.html` | Dashboard hub; biggest surprises, country overview cards |
| `/countries` | `countries.html` | Grid of all 8 tracked countries |
| `/country/{code}` | `country.html` | Per-country indicator breakdown |
| `/country/{code}/tab/{category}` | `_country_tab.html` | Filtered by category (Inflation, Growth, Labor, Monetary Policy, Trade, Sentiment, Housing, Other) |
| `/country/{code}/indicator/{name}` | `indicator.html` | Full indicator time-series chart, history table, revision toggle |
| `/calendar` | `calendar.html` | Economic calendar with country/category/importance filters |
| `/rates` | `rates.html` | Multi-currency 10Y yield curves and FX pair charts |
| `/cot` | `cot.html` | CFTC Commitment of Traders positioning by market |
| `/news-feed` | `news_feed.html` | InvestingLive RSS headlines with auto-categorization |
| `/cb-news` | `cb_news.html` | Central bank RSS feeds from 8 banks with OpenAI stance analysis |
| `/fx-outlook` | `fx_outlook.html` | 8-currency strength meter with CB-preferred indicator breakdown |
| `/trade-planner` | `trade_planner.html` | Interactive trade planning grid (scaffolding) |
| `/brief-builder` | `brief_builder.html` | Research brief builder (scaffolding) |
| `/bank-research` | `bank_research.html` | Google Drive research reports with AI summaries |
| `/bank-research/admin` | `bank_research_admin.html` | Admin panel for Drive folder config and refresh |
| `/analytics` | `analytics.html` | Data warehouse stats; coverage %, category mix, ingestion runs, CSV/PDF export |
| `/settings` | `settings.html` | User preferences |
| `/users` | `users.html` | Admin user management (create, role, deactivate) |
| `/login` | `login.html` | Email/password authentication |
| `/setup` | `setup.html` | First-run admin user creation |

### JSON API (`app/api/routes/public.py`, `admin.py`)

**Public:**
- `GET /api/countries` — country summaries
- `GET /api/surprises` — biggest recent indicator surprises
- `GET /api/calendar` — economic calendar events
- `GET /api/country/{code}` — country detail payload
- `GET /api/indicators/{id}` — indicator with release history
- `GET /api/country/{code}/indicator/{name}` — indicator explorer payload
- `GET /api/cot` — COT positioning by pair
- `GET /api/news/feed` — InvestingLive RSS headlines
- `POST /api/news/sentiment` — OpenAI sentiment analysis on a headline
- `GET /api/cb/feeds` — Central bank RSS feeds (8 banks)
- `POST /api/cb/analysis` — OpenAI CB stance analysis

**Admin:**
- `GET /api/admin/health` — system health check
- `GET /api/admin/ingestion-runs` — paginated ingestion run history
- `GET /api/admin/unmapped-events` — unmapped EODHD events awaiting mapping

---

## External Integrations

### EODHD API
Economic calendar events for 8 countries. Also used for EOD price data (10Y bond yields, FX pairs) on the Rates page.

### OpenAI API (gpt-4o-mini, configurable)
- Sentiment analysis on news headlines → bullish/bearish/neutral + asset impact + themes
- CB policy stance analysis from RSS feed snippets → hawkish/dovish bias + risk factors
- Bank research report summarization → key themes + outlook + data points
- API key never reaches the browser (server-side only)

### Google Drive API
Lists and downloads bank research reports (PDF, DOCX, TXT) from a configurable Drive folder. Text is extracted and sent to OpenAI for summarization. Files older than 7 days are auto-deleted.

### CFTC COT Data
Historical CSV files for FX futures and commodities. 6-hour in-memory cache. Markets: EUR, GBP, JPY, CAD, CHF, AUD, NZD, MXN, USD Index, Gold, Crude Oil.

### Central Bank RSS Feeds (8 feeds)
Fed, ECB, BoE, BoJ, RBA, BoC, SNB, RBNZ. 15-minute TTL cache per feed. OpenAI analyzes top 3 articles per bank for policy stance.

### InvestingLive RSS
40 latest macro headlines with auto-categorization (FX, Central Banks, Equities, Geopolitical, Macro) via regex pattern matching.

---

## Key Features

### Revision Tracking
Each indicator release is versioned. "As-reported" mode shows the value at original publication (no look-ahead bias for backtesting). "Latest" mode shows the current official revision. `is_latest` flag on `indicator_releases` tracks this.

### CB Preferred Score
Each central bank has a curated watchlist of indicators aligned to its mandate (e.g., Fed watches Core PCE, NFP, AHE; ECB watches Core HICP, Unemployment, GDP). Scores are mandate-weighted (e.g., Fed dual mandate: 1.5× inflation + 1.0× labor + 0.5× growth) and Z-scored across 8 currency peers on the same date.

### Currency Stance
Computes inflation/labor/growth stance scores per currency with trend labels. Displayed on the FX Outlook page with lookback windows (Current / 1M / 3M / 6M ago).

### Analytics Reporting
The `/analytics` page aggregates: total releases, mapped %, category mix, country depth, recent ingestion run audit trail. Exportable as CSV or PDF (via ReportLab).

---

## Authentication & Authorization

- Session cookies signed with a secret key; max age 12 hours
- Roles: `admin`, `analyst`, `viewer`
- `require_role("admin")` dependency restricts admin endpoints
- Passwords hashed with Argon2 (via passlib)
- First-run `/setup` endpoint creates the initial admin user (one-time only)

---

## Configuration

**Environment variables (`.env`):**
| Variable | Purpose |
|---|---|
| `EODHD_API_KEY` | EODHD subscription key (required) |
| `DATABASE_URL` | Async PostgreSQL connection string (required) |
| `AUTH_SECRET_KEY` | Session signing key |
| `OPENAI_API_KEY` | OpenAI key (optional) |
| `OPENAI_MODEL` | Model name (default: gpt-4o-mini) |
| `GOOGLE_DRIVE_API_KEY` | Google Drive key (optional) |
| `BANK_RESEARCH_DRIVE_FOLDER_URL` | Drive folder URL |
| `BANK_RESEARCH_RETENTION_DAYS` | Report cache TTL (default: 7) |
| `ENABLE_SCHEDULER` | Toggle background jobs (default: true) |
| `AUTH_ENABLED` | Require login (default: true) |
| `HTTP_TIMEOUT_SECONDS` | HTTP timeout (default: 30.0) |
| `HTTP_MAX_RETRIES` | Retry attempts (default: 3) |

**YAML config files (`config/`):**
- `countries.yaml` — Static country reference data (8 countries, CB info, mandate type, inflation target)
- `indicator_mapping.yaml` — Raw EODHD event type → canonical name mappings (~4,500 entries)
- `release_schedule.yaml` — Post-release trigger times for major indicators

---

## Tracked Countries & Currencies

| Country | Currency | Central Bank | Mandate |
|---|---|---|---|
| United States | USD | Federal Reserve | Dual (inflation + employment) |
| Eurozone | EUR | ECB | Single (price stability) |
| United Kingdom | GBP | Bank of England | Inflation |
| Japan | JPY | Bank of Japan | Inflation |
| Australia | AUD | RBA | Dual |
| New Zealand | NZD | RBNZ | Dual |
| Canada | CAD | Bank of Canada | Inflation |
| Switzerland | CHF | SNB | Single |

---

## Project Structure

```
macro-dashboard/
├── app/
│   ├── main.py                  # FastAPI entry point, lifespan, scheduler startup
│   ├── settings.py              # Pydantic settings from environment
│   ├── auth.py                  # Auth middleware, session validation, role checks
│   ├── db/
│   │   ├── models.py            # ORM models
│   │   └── session.py           # Async DB session factory
│   ├── api/routes/
│   │   ├── pages.py             # All HTML page routes
│   │   ├── public.py            # JSON API (public)
│   │   ├── admin.py             # JSON API (admin)
│   │   └── auth.py              # Login, setup, user admin routes
│   ├── ingestion/
│   │   ├── scheduler.py         # APScheduler configuration
│   │   ├── eodhd_client.py      # EODHD API client
│   │   ├── ingest_service.py    # DB write logic with revision handling
│   │   ├── canonicalizer.py     # Event type → canonical name mapping
│   │   └── run_logger.py        # Ingestion audit logging
│   ├── processing/              # 19 batch analytical modules
│   └── web/
│       ├── templates/           # 21 Jinja2 HTML templates
│       └── static/
│           ├── js/              # 10 JavaScript modules
│           └── css/
├── config/
│   ├── countries.yaml
│   ├── indicator_mapping.yaml
│   └── release_schedule.yaml
├── data/                        # Generated outputs (COT cache, research files, processed CSVs)
├── migrations/                  # Alembic database migrations
├── tests/
└── scripts/                     # Seed, remap, and analysis scripts
```
