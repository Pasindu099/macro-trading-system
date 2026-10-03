Read AGENTS.md, PROGRESS.md, DECISIONS_STEP4.md, REPORT_STEP4.md first.
Save this prompt verbatim to DECISIONS_STEP5.md. Add Step 5 sub-tasks to
PROGRESS.md; update and commit after each part. Backend only.

PART 0 — RATES DATA INTEGRITY (from Step 4 Part C)
- For GBPUSD, NZDUSD, AUDUSD, USDCHF and JP10Y, compare stored raw payload
  vs current live EODHD values per date in the Part C window. Classify each
  gap: provider revision after ingestion, date shift (off by one day), or
  other. Max 20 lines in REPORT_STEP5.md.
- If revisions: make incremental yield and FX ingestion re-fetch the last
  5 business days and update changed values; then re-run outlier flagging
  and spreads for affected dates (job:rates_derived handles it).
- If a date shift: fix the date mapping, add a test, and re-ingest the
  affected window.

PART A — COT CURRENT STATE (report only, ≤15 lines)
- processing/cot.py: which CFTC report (Legacy or Traders in Financial
  Futures), source URL, contracts used, cache behaviour.
- Confirm whether the TFF report includes the ICE US Dollar Index (DX).

PART B — PERSISTENCE + BACKFILL
- Migration 0023: table cot_positions: report_date, contract_code,
  currency, category (dealer, asset_manager, leveraged_funds,
  other_reportable, nonreportable), long, short, spreading, open_interest,
  PK (report_date, contract_code, category).
- config/cot_contracts.yaml: contract per currency (EUR, GBP, JPY, AUD,
  NZD, CAD, CHF futures; USD via DX if in TFF, else derived from the
  other seven and flagged derived=true).
- Backfill TFF history from 2010 using CFTC's yearly historical files.
  Idempotent upserts.
- Weekly job "job:cot_weekly": Friday 21:00 UTC, retry Monday 21:00 UTC
  if the expected report_date is missing (holiday delays). Existing job
  framework (lock, timeout, logging, status endpoint).

PART C — FX HISTORY FOR POSITIONING STATS
- Extend FX spot backfill for the 7 USD majors back to 2010-01-01
  (one request per pair). Report calls used.

PART D — DERIVED METRICS (service app/services/positioning.py)
- Net position per category (long − short), as contracts and % of OI.
- Percentile of net position over 1y / 3y / 5y lookbacks
  (0 = most short, 100 = most long).
- 1W and 4W change in net position.
- Crowding: >= 85 crowded_long, <= 15 crowded_short (leveraged funds and
  asset managers separately).
- Squeeze watch per currency: crowded AND spot moved against the crowd
  over the last 2 weeks AND net position reduced for 2 consecutive weeks
  → "high"; crowded + one of the other two → "medium"; crowded and price
  still moving with the crowd → "trend_confirming".
- After-extremes stats: for each currency and threshold band (>90, 85–90,
  10–15, <10 percentile, leveraged funds, 3y lookback), the number of
  episodes (first week entering the band, non-overlapping), average spot
  move 4W and 8W after, and % reversed within 8W.
- Pair-implied positioning for any of the 28 pairs: base leveraged-funds
  percentile minus quote's (no cross-currency futures exist).
Use the report_date (Tuesday) for alignment with spot, never the release date.

PART E — APIs (viewer auth)
GET /api/positioning/crowding?lookback=3y
GET /api/positioning/{currency}?weeks=52
GET /api/positioning/flows?window=1W|4W
GET /api/positioning/squeeze
GET /api/positioning/extremes/{currency}
GET /api/positioning/pair/{pair}

TESTS
- Percentile edges (min → 0, max → 100), lookback windows.
- Squeeze rule: high / medium / trend_confirming cases.
- After-extremes: overlapping episodes counted once.
- Alignment uses report_date; holiday-delayed report handled.
- Idempotent backfill (re-run writes no duplicates).
- Part 0 fix has a regression test.
- Full suite once per part (known FED test deselected).

DO NOT: UI work; change existing rates logic beyond Part 0; new dependencies.
DELIVER: REPORT_STEP5.md (≤60 lines) with Part 0 verdict, contracts used,
backfill counts, calls used, endpoints, test results.
