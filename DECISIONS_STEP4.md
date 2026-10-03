STEP 4 — RATES DATA LAYER (backend only)

SETUP: Save this prompt verbatim to DECISIONS_STEP4.md. Add Step 4
sub-tasks to PROGRESS.md; update and commit after each part.

PART A — COVERAGE REPORT (write REPORT_STEP4_COVERAGE.md, then continue)
- government_yield_observations: for each country (US, EZ/DE, FR, GB, JP,
  AU, NZ, CA, CH) and tenor (2Y, 10Y, 30Y, plus whatever else exists):
  first date, last date, row count, gaps > 5 business days.
- fx_spot_observations: same for the 28 G10 pairs or whatever exists.
- Report whether the yield backfill completed (checkpoint files in data/).
- Find the authoritative POLICY RATE source per CB in the DB (cb_meetings,
  rate_snapshots, ois_cache or config) and report which one you will use.
Continue unless a whole country/tenor is missing; then list the
fallback you propose (e.g. 20Y for missing 30Y) and stop.

PART B — CONFIG: config/pairs.yaml
- All 28 pairs in market convention (priority EUR > GBP > AUD > NZD >
  USD > CAD > CHF > JPY): base, quote, pip_size (0.01 for JPY pairs,
  0.0001 otherwise).
- Yield benchmark per currency: EUR uses Germany (DE); also define the
  intra-EUR spread FR−DE (OAT–Bund) as a named spread.
- Tenor fallbacks per country where 30Y is missing (from Part A).

PART C — SWITCH rates SERVICE TO DB READS
- Replace the live EODHD calls in app/services/rates.py with reads from
  government_yield_observations / fx_spot_observations (remove the TODO).
- Before switching, record outputs from the live version for a fixed
  date window; after switching, compare on overlapping dates and report
  the max absolute difference per series in the report. Investigate
  any difference > 1bp (yields) or > 0.05% (FX).

PART D — DATA QUALITY: OUTLIER FLAG
- Migration 0022: add is_outlier boolean NOT NULL DEFAULT false to
  government_yield_observations and fx_spot_observations.
- Rule: daily change > 6σ (σ from the prior 250 daily changes) AND the
  next day's change reverses it (opposite sign, also > 6σ) → flag that
  observation. Never delete raw data.
- All services exclude flagged rows. Log flags to ingestion_runs.

PART E — SPREADS
- Table yield_spreads (migration 0022): spread_name, tenor, obs_date,
  base_yield, quote_yield, spread_bp, PK (spread_name, tenor, obs_date).
- Spread = base − quote in bp, per pairs.yaml (USDJPY = US − JP,
  EURUSD = DE − US, AUDCAD = AU − CA, plus FR−DE).
- Alignment: common dates only; forward-fill gaps of at most 2 business
  days; never more.
- Tenors: 2Y and 10Y for all 28 pairs; 30Y where both sides exist.

PART F — CURVE METRICS AND REGIMES (service functions, no table needed)
For each country:
- 2s10s and 10s30s (bp), 2Y − policy rate (bp).
- Regime over windows 1W / 1M / 3M using changes d2 (2Y) and d30 (30Y,
  or the fallback tenor), in bp:
    both > 0:  d2 >= d30 → "bear_flattener", else "bear_steepener"
    both < 0:  |d2| >= |d30| → "bull_steepener", else "bull_flattener"
    d2 <= 0 < d30 → "twist_steepener"
    otherwise → "twist_flattener"
- Inversion: 2s10s < 0. Un-inversion event: 2s10s crosses above 0 after
  at least 90 calendar days inverted; return the date of the most recent
  one.

PART G — DRIVER CORRELATIONS (service)
- For a pair and a spread/tenor: correlation of DAILY CHANGES (not
  levels) with spot over the last 60 and last 15 observations, plus
  latest level and 20-day change.
- Status: |c60| < 0.25 → "weak_link"; c60 − c15 > 0.45 → "diverging";
  else "aligned".

PART H — JOB + APIs
- Job "job:rates_derived" runs after each successful gov-yield incremental
  ingest: outlier flagging, then spreads. Use the existing job framework
  (advisory lock, statement_timeout, ingestion_runs logging, status
  endpoint).
- JSON endpoints (service-backed, auth required):
  GET /api/rates/curve/{country}?window=1M
  GET /api/rates/spreads/{pair}?tenor=2Y&days=250
  GET /api/rates/regimes   (all countries, all windows)
  GET /api/rates/drivers/{pair}   (Part G for 2Y and 10Y spreads)

TESTS / CHECKPOINTS
1. Regime classification, table-driven, including:
   d2=−4, d30=+3 → twist_steepener; d2=+18, d30=+4 → bear_flattener;
   d2=+40, d30=−2 → twist_flattener; d2=−20, d30=−5 → bull_steepener;
   d2=−3, d30=−12 → bull_flattener; d2=+5, d30=+15 → bear_steepener.
2. Spread sign convention for USDJPY, EURUSD, AUDCAD, FR−DE.
3. Forward-fill: fills 1–2 day gaps, never 3+.
4. Outlier: one-day spike that reverses is flagged; a genuine level shift
   that does not reverse is NOT flagged.
5. Correlation uses daily changes (test with two trending series whose
   changes are uncorrelated: c60 ≈ 0 even though levels correlate).
6. Un-inversion detection: requires ≥ 90 days inverted.
7. Part C comparison results in the report.
8. Full unit suite + integration suite pass (known FED test excepted).

DO NOT
- No UI, templates or frontend work.
- Do not change yield/FX ingestion logic except adding the is_outlier
  column and the flagging job.
- No new dependencies.
- Do not modify existing migrations.

DELIVER: REPORT_STEP4.md (files, migration, endpoints, Part A/C findings,
test results) and the resume-safe PROGRESS.md.

STEP 4 — DECISION ON COVERAGE GAPS

1. APPROVED: proceed with Parts B–H now using available data.
   - Curve regime uses 2Y vs 10Y (d2 vs d10) as the standard definition,
     tenor recorded explicitly in every output. This is permanent, not
     a fallback.
   - 10s30s, 30Y-based checks and FR−DE: return "unavailable" with a
     reason field until data exists. No errors, no fake values.
   - Correlations/drivers for pairs without spot history: "unavailable".

2. ADD PART I — SOURCE MISSING DATA (after Part H passes). This
   explicitly lifts the "do not change ingestion" rule for this part only.
   a) Availability check first (write REPORT_STEP4_SOURCING.md):
      - Use the existing GBOND symbol discovery (_fetch_gbond_symbol_set
        in app/services/rates.py) to list available tenors for US, DE,
        FR, GB, JP, AU, NZ, CA, CH. Report 2Y/10Y/20Y/30Y availability.
      - Check EODHD FOREX symbols for the 14 missing pairs.
   b) Extend the existing gov-yield ingestion config to include:
      - FR 2Y and 10Y
      - 30Y for every country where available (20Y where 30Y is not)
      Backfill to the same start date as existing yield history, using the
      existing backfill/checkpoint mechanism. Respect the 50K calls/day
      limit; report calls used.
   c) Missing FX pairs: ingest directly from EODHD where available
      (same pipeline as existing pairs, same backfill depth). Only if a
      pair is NOT available, derive it synthetically from the two USD
      legs on common dates, store it with source = "synthetic", and
      exclude synthetic pairs from the outlier flagging rule.
   d) After backfill: run job:rates_derived, then confirm 10s30s, FR−DE
      and the new pairs' drivers now return values. Update pairs.yaml
      tenor fallbacks to match what actually exists.

3. TESTS for Part I: config includes new symbols; synthetic cross
   equals leg product within rounding; unavailable → available
   transition works without code changes.

Continue now with Part B. Update PROGRESS.md and commit per part.
