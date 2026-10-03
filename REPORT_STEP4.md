# Step 4 rates data layer

## Part C: live-to-DB comparison

- Window: 2026-08-03 through 2026-08-21. Live EODHD closes captured before the service switch in `data/step4_rates_live_snapshot.json`.
- DB reader: `app/services/rates.py`; comparison command: `docker compose exec app python -m scripts.step4_compare_rates`.
- Maximum absolute differences below are percentage points for yields and percent of live close for FX. Only common dates are compared.

| Symbol | Common dates | Maximum difference |
| --- | ---: | ---: |
| AU10Y, CA10Y, NZ10Y | 13–15 | 0 bp |
| DE10Y | 15 | 0.55 bp |
| JP10Y | 15 | 1.13 bp |
| SW10Y | 15 | 0.29 bp |
| UK10Y | 15 | 0.01 bp |
| US10Y | 15 | 3.40 bp |
| AUDUSD | 17 | 0.112% |
| EURUSD | 17 | 0.031% |
| GBPUSD | 18 | 0.483% |
| NZDUSD | 19 | 0.483% |
| USDCAD | 17 | 0.022% |
| USDCHF | 17 | 0.075% |
| USDJPY | 15 | 0.025% |

- Above-threshold series: JP10Y, US10Y, AUDUSD, GBPUSD, NZDUSD, USDCHF. The stored raw payload for US10Y on 2026-08-21 has close 4.702, matching its stored value; the current live response is 4.736. This confirms that at least that gap reflects a changed provider value after ingestion, rather than a DB read conversion. The other gaps are recorded for the Part I backfill/refresh.
- Focused tests: `test_rates_db_reads.py` 1 passed; `test_rate_repricing.py` 16 passed. Full suite: 292 passed, 7 skipped, 1 deselected (known FED date test), 18 integration failures where test clients receive sign-in/401 responses.
