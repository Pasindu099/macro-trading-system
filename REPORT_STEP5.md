# Step 5 — Rates integrity + COT positioning

## Part 0: rates data integrity — verdict: provider revision, no date shift

Window 2026-08-03..21; stored `raw_payload` vs live EODHD now (`scripts/step5_rates_integrity.py`, 5 history calls). The Step 4 snapshot equals live-now on every date, so nothing moved after Step 4 Part C.

| Symbol | Stored rows ≠ live | Classification |
| --- | ---: | --- |
| GBPUSD | 18/18 | Revision: provider re-sourced history (4-dp, volume 0 → 6-dp bars). Old bar's close ≈ prior day's close while high/low match the same day, so it looks like a 1-day shift, but `raw_payload.date` equals our `observation_date`. Our mapping is correct. |
| NZDUSD | 18/19 | Revision: same re-sourced feed; differences up to 0.48%. |
| AUDUSD | 17/17 | Revision: 4-dp vs 6-dp precision on 16 dates; 2026-08-21 (0.08%) was captured intraday before the close. |
| USDCHF | 1/17 | Revision: 2026-08-21 only, captured intraday on ingestion day. |
| JP10Y | 1/16 | Revision: 2026-08-21 only (2.886 → 2.8747), captured intraday. |

- Fix: the daily rates job re-fetches at least 5 business days (`revision_refetch_start`, `app/services/government_yields.py`). It now also re-fetches all 28 EODHD FX pairs, which previously had no incremental ingest (`app/ingestion/scheduler.py`, `_run_fx_spot_incremental`). It then runs `job:rates_derived` when FX rows change.
- A revised payload has a new hash, so it is inserted as a new row. Every reader already takes the newest row per date, and the superseded raw payload is retained.
- Re-ingest: yields 2026-08-01..10-03 (58 calls, 920 rows, 147 revised symbol-dates). 7 original crosses since 2023-08-21 (7 calls, 265 rows). The 7 USD majors were re-fetched from 2010 in Part C. `job:rates_derived`: 0 new flags, 60,982 spreads.
- Regression test: `tests/unit/test_rates_revision_refetch.py` covers the 5-business-day window, the revised hash on the same date, and the job re-fetching FX and yields before the rebuild.
- Verification after Part C re-ingest: `scripts.step4_compare_rates` shows 0 difference against live on all 15 Step 4 series (max 0.0). Full suite: 336 passed, 7 skipped, 1 deselected (known FED test).

## Part A: COT current state

- `app/processing/cot.py` reads the **Legacy** futures-only report (noncommercial/commercial/nonreportable), not TFF.
- Source: `https://www.cftc.gov/files/dea/history/deacot{year}.zip` for the current and 2 prior years (`cot.py:161`). `CFTC_BASE_URL` (newcot) is unused.
- Contracts are matched by name prefix: EUR, GBP, JPY, CAD, CHF, AUD, NZD, MXN, USD INDEX, GOLD and WTI. No contract codes are used.
- Cache: in-process, 6-hour TTL (`COT_CACHE_TTL`) and not persisted. Download failures are swallowed per year, and payloads use the last 12 weeks.
- TFF (`fut_fin_txt_{year}.zip`) **includes ICE US Dollar Index DX, code 098662, in every year 2010–2026** (39 weeks in 2026 to 2026-09-29). USD is therefore sourced directly (`derived: false`).
- TFF market names drift (e.g. "BRITISH POUND STERLING" → "BRITISH POUND"), and the date header changes between years (`Report_Date_as_MM_DD_YYYY` → `Report_Date_as_YYYY-MM-DD`). Step 5 therefore keys on contract codes and `As_of_Date_In_Form_YYMMDD`.
- The 2010 yearly file starts mid-year (24 weeks). Jan–Jun 2010 comes from `fin_fut_txt_2006_2016.zip`, filtered to ≥ 2010-01-01.

## Part B: persistence + backfill

- Migration `2026_10_03_0023_cot_positions.py`: PK (report_date, contract_code, category), category check, (currency, report_date) index.
- Contracts used (`config/cot_contracts.yaml`, TFF futures only): EUR 099741, GBP 096742, JPY 097741, AUD 232741, NZD 112741, CAD 090741, CHF 092741 (all CME), and USD = ICE DX 098662 (`derived: false`).
- Backfill (`scripts/backfill_cot_tff.py`): 18 CFTC downloads (combined 2006–16 + yearly 2010–2026), 48,440 rows seen, **34,960 written** = 874 weeks × 8 contracts × 5 categories, 2010-01-05 → 2026-09-29. The 2010–2016 yearly files wrote 0 rows because they match the combined file. A re-run of 2025–26 wrote 0.
- `job:cot_weekly` (`app/services/cot_positions.py`) runs Fri 21:00 UTC and retries Mon 21:00 UTC. The job uses an advisory lock, `run_logger`, a 10-minute guard and a 5-minute statement timeout. The Monday retry is skipped if the expected Tuesday is stored. If that Tuesday is still missing, the run is logged `partial`.
- Tests: 4 unit (`test_cot_positions.py`), 1 integration idempotency (`test_cot_backfill.py`). Full suite: 341 passed, 7 skipped, 1 deselected.

## Part C: FX history for positioning

- `backfill_fx_spot` for EUR/USD, GBP/USD, USD/JPY, AUD/USD, NZD/USD, USD/CAD and USD/CHF, 2010-01-01 → 2026-10-03, used **8 EODHD calls**: 7 history calls (one per pair) and 1 FOREX listing call. 33,270 rows were seen and 31,354 inserted. All 7 pairs now start 2010-01-01.
- `job:rates_derived` afterwards: 0 yield flags, 1 FX flag (historical reversing spike), 60,982 spreads.
- Step 5 EODHD total so far: 5 diagnostic + 59 yield + 8 cross + 8 major = 80 calls (listing calls included).

## Part D: derived metrics (`app/services/positioning.py`)

- Net = long − short per category, in contracts and % of OI. Percentiles cover 1y/3y/5y calendar windows that include the current week, with ties at the midpoint. A percentile is withheld until the window holds 90% of its weeks. Changes are 1W and 4W. Crowding is ≥85 long and ≤15 short, for leveraged funds and asset managers separately.
- Spot is aligned to the report_date (Tuesday): the newest valid close on or before it, at most 5 days stale. Currency direction is vs USD. USD itself uses an equal-weight geometric index of the 7 majors, because no DXY spot is stored.
- Squeeze checks the last 3 weekly nets and the 2-week spot move between report dates. Extremes use leveraged funds and the 3y lookback. An episode is the first week in a band, and no new episode starts within 8 weeks of the previous one. A move counts as reversed when the 8W move goes against the band's crowd.
- Latest (2026-09-29, 3y, LF): crowded short EUR 8.3, GBP 4.5, CHF 13.5; crowded long AUD 96.8 (squeeze `medium`).

## Part E: APIs (viewer auth when enabled; `app/api/routes/positioning.py`)

`GET /api/positioning/crowding?lookback=1y|3y|5y` · `/{currency}?weeks=52` · `/flows?window=1W|4W` · `/squeeze` · `/extremes/{currency}` · `/pair/{pair}`. Unknown currency or pair returns 404, and invalid query values return 422.

## Tests and production commands

- New tests: positioning 15, positioning API 3, COT 4, revision refetch 3; integration: COT idempotency 1, positioning endpoints 1.
- Final full suite: **360 passed, 7 skipped, 1 deselected** (known FED test). Integration suite: 25 passed.
- Production: `alembic upgrade head` → `python -m scripts.backfill_cot_tff` (CFTC, no EODHD calls) → `python -m scripts.backfill_fx_spot --pairs EUR/USD GBP/USD USD/JPY AUD/USD NZD/USD USD/CAD USD/CHF --start 2010-01-01 --end <today> --max-requests 7 --summary-only` → `python -m scripts.run_rates_derived`.
