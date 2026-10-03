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

## Part D: outlier flags

- Migration `0022_rates_quality_spreads` applied locally. It adds `is_outlier` to both observation tables and creates the Part E `yield_spreads` table.
- The reversing-spike job flagged 13 government yields and 0 FX observations. Raw rows remain; service and fixed-income reads exclude flagged rows. Synthetic FX observations are excluded from the flagger.
- Focused tests: quality and DB reads 3 passed; fixed-income API 7 passed. Full suite (before the final fixed-income filter edit): 294 passed, 7 skipped, 1 deselected, same 18 auth-related integration failures.

## Part E: yield spreads

- `app/services/yield_spreads.py` builds base-minus-quote spreads from valid, non-outlier observations. It aligns print dates and carries a missing leg for at most two business days.
- Local rebuild wrote 45,200 rows. Both 2Y and 10Y cover all 28 currency pairs; 30Y and FR−DE have no rows because their source yields are absent.
- Focused tests: 2 passed. The full-suite run yielded before its final result was captured; prior Part D full-suite result is above.
