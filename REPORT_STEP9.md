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
