# Step 9 — EUR desk groundwork

## Part 0: null-period releases

- `app/ingestion/ingest_service.py:205`: both-null periods now match on indicator and release date; same-day corrections revise that print.
- `scripts/repair_null_period_latest.py`: dry-run found **544** flag changes across the 23 indicators named in `REPORT_NULL_PERIODS.md`; real run applied 544 and rescored **911** Event Innovation rows in the same transaction. Repeat dry-run: **0** changes.
- Rows changed by indicator:
  - AU: cash_rate 39; private_sector_credit_mom 1; retail_sales_mom 0; td_mi_inflation_gauge_mom 0.
  - CA: overnight_rate 51; vehicle_sales_mom 0; wholesale_sales_mom 1.
  - CH: policy_rate 18.
  - EU: ecb_deposit_rate 32; ecb_marginal_lending_rate 2.
  - JP: adjusted_trade_balance 13; balance_of_trade 0; boj_interest_rate_decision 14; machine_tool_orders_yoy 0.
  - NZ: global_dairy_trade_price_index 156; imports 5; milk_auctions 34; official_cash_rate 31.
  - UK: bank_rate 45; house_price_index_mom 0; house_price_index_yoy 31.
  - US: avg_hourly_earnings_level 15; fed_interest_rate_decision 56.
- Six additional null-period indicators appeared since the prior report; this repair was scoped to the specified 23. The ingestion fix applies to future prints for all indicators.
- `app/services/curve_metrics.py:126` again filters `is_latest`. Focused tests: 11 passed, including the PostgreSQL different-date/same-day correction test.
- Full suite: **482 passed, 7 skipped, nothing deselected**. The new integration test closes its database pool before subsequent TestClient loops.

## Part A: EUR config

- `config/desks.yaml`: EUR enabled with ECB, DE yield benchmark and EZ/DE/FR members (`EZ` maps to database `EU`). GDP labels explicitly say QoQ, not annualised.
- `scripts/check_desk_indicators.py`: EUR charts 4/4 mapped. Country key-data gaps: EZ wage growth; DE and FR core HICP and wage growth. These are null/unavailable, with no substitute.
- Existing USD checker gap remains ISM manufacturing production (unrelated).
- `config/central_banks.yaml`: ECB `verify: false`; Governing Council corrected to 27 participants and 21 voting rights in 2026 ([ECB voting rotation](https://www.ecb.europa.eu/ecb-and-you/explainers/tell-me-more/html/voting-rotation.ff.html)). Symmetric 2% HICP target ([ECB strategy](https://www.ecb.europa.eu/mopo/strategy/strategy-review/ecb.strategyreview202506_strategy_statement.en.html)) and quarterly projection cadence ([ECB projections](https://www.ecb.europa.eu/press/projections/html/index.en.html)) confirmed.
- Focused desk and central-bank tests: 8 passed. Full suite: **482 passed, 7 skipped, nothing deselected**.

## Part B: country monitor

- `app/services/country_monitor.py`: latest available mapped prints for EZ/DE/FR; comparison tones relative to EZ, inflation hotter/cooler, unemployment direction inverted. Unmapped data stays unavailable.
- Migration `2026_10_03_0026_country_fiscal_observations.py` stores annual general-government balance as % GDP; `scripts/ingest_eurostat_deficit.py` reads [Eurostat `gov_10dd_edpt1`](https://ec.europa.eu/eurostat/databrowser/view/gov_10dd_edpt1/default/table), with dry-run. Loaded 18 rows (2020–2025): 2025 deficits EZ 2.9%, DE 2.7%, FR 5.1% of GDP.
- FR–DE 10Y from existing `yield_spreads`: 139.07 bp as of 2026-10-02. `country` panel routes only for desks with `members`; USD returns 404 and its panel list is unchanged.
- Focused country/desk tests: 46 passed; live EUR panel rendered HTTP 200 with deficit and spread. Full suite: **485 passed, 7 skipped, nothing deselected**.

## Part C: ECB projections and tracking

- [ECB MPD](https://data.ecb.europa.eu/data/datasets/MPD/data-information) supplies annual HICP, core HICP, real GDP and unemployment point projections. `app/services/ecb_projections.py` parses quarterly MPD exercises from 2020, validates bounds, and writes `cb_projection_values` with `stat=median`; round dates use the first day of the exercise month because MPD omits the publication day.
- [ECB seasonally adjusted HICP index](https://data.ecb.europa.eu/data/datasets/HICP/HICP.M.U2.Y.000000.4F0.INX) feeds the Fed-equivalent Q4 required-pace vs 3m annualised rule. Migration `0028_ecb_series_observations` stores ECB series; `scripts/load_ecb_projections.py` and `scripts/load_ecb_hicp.py` are the idempotent loaders.
- ECB deposit-rate regime reuses the Fed rate/last-move classifier. End-horizon HICP minus 2% produces dovish/neutral/hawkish at ±0.1pp, with no bp value. `/api/cb/ECB/*` exposes projections, tracking, regime and method-labelled gap; dots/risk counts explicitly unavailable.
- Saved MPD fixture: `tests/fixtures/ecb/mpd_w24.csv`. Focused tests: 7 passed; full suite: **506 passed, 7 skipped**. The local container timed out twice on the ECB API, so live loading awaits an environment with source access; no production request was made.
