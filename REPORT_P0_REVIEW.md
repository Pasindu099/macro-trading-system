# P0 surprise calibration review — local database, 5 Oct 2026

## Floor calibration
- Old denominator: `max(prior five-print std, 0.5 native units, 5% of |prior five-print mean|)` (`app/processing/macro_features.py` before this change; SQL window `5 PRECEDING` to `1 PRECEDING`). It bound 11,757 of 25,785 eligible feature rows (45.6%). Full per-indicator counts: [REPORT_P0_FLOOR_BINDING.csv](REPORT_P0_FLOOR_BINDING.csv); reproducible query: `scripts/_p0_review_diagnostics.sql`.
- Unit grouping uses canonical `_yoy`, `_mom`/`_qoq`, then declared unit. `other` includes rates, counts, hours and unlabelled series. Ties at exactly 0.5 are counted in the absolute column; absolute/relative columns may overlap at ties.

| Unit group | Eligible | Old floor binds | % | New long-run anchor binds |
|---|---:|---:|---:|---:|
| YoY % | 5,567 | 2,151 | 38.6 | 762 |
| MoM/QoQ % | 6,371 | 2,851 | 44.7 | 435 |
| Level index | 5,778 | 3,320 | 57.5 | 535 |
| Balance | 77 | 3 | 3.9 | 0 |
| Signed flow | 4,690 | 1,312 | 28.0 | 1,402 |
| Other | 3,302 | 2,120 | 64.2 | 267 |

- New denominator: `max(std of up to 24 prior observations, 0.25 × prior expanding std)`. The five-print mean remains the recent reference, avoiding a two-year mean lag. A quarter of each series' historical scale prevents near-zero local variance while preserving its native unit; 24 monthly prints cover roughly two years, with available shorter history used initially. No static 0.5 or percent-of-level threshold remains (`app/processing/macro_features.py`).
- EU September HICP: actual 3.8%, previous 3.2%, EODHD estimate 3.6%; five-print mean 3.02%, std 0.179. Old scale 0.5 → z=1.560; new 24-print std 0.440, long-run std 2.836, scale 0.709 → z=1.100. The score is a recent-level deviation, **not** the actual-minus-estimate surprise (0.2pp).

| Inflation component | Old | New | Main latest headline CPI z, old → new |
|---|---:|---:|---:|
| USD | -0.590 | -0.940 | -0.169 → -0.180 |
| CHF | +1.820 | +1.542 | +0.840 → +1.348 |
| NZD | +2.040 | +0.805 | +2.440 → +0.590 |
| GBP | +0.016 | -0.070 | +0.320 → +0.207 |
| EUR | +0.415 | +0.208 | +1.560 → +1.100 |

## Caps
- The previous final build logged **334** values beyond ±5; 335 stored values equalled ±5 because one naturally equalled the bound. [REPORT_P0_CAP_DATES.csv](REPORT_P0_CAP_DATES.csv) lists the 334 by currency, indicator and date; [REPORT_P0_CAP_SERIES.csv](REPORT_P0_CAP_SERIES.csv) gives series counts and first/last dates. Currency counts: EUR 70, USD 62, JPY 52, GBP 45, CAD 43, AUD 36, NZD 17, CHF 9 (334 total). The diagnostic reconstruction found additional candidate dates where subsequent observations or series windows differ; the exact stored-cap count and first/last bounds resolve all but EUR GDP QoQ, whose two stored dates are its first and last bounds. New build: 289 values at ±5, including naturally equal values.

| Top old series | Caps | Classification from print pattern and new cap count |
|---|---:|---|
| CAD budget_balance | 10 | Signed fiscal flow; short-window problem; 2 new caps. |
| JPY foreign_bond_investment | 9 | Volatile signed flow; short-window problem; 1 new cap. |
| JPY stock_investment_by_foreigners | 8 | Volatile signed flow; short-window problem; 3 new caps. |
| EUR zew_economic_sentiment | 7 | COVID shock plus short-window problem; 2 new caps. |
| EUR retail_sales_yoy | 6 | COVID/reopening fat tails; 2 new caps. |
| NZD global_dairy_trade_price_index | 6 | Short-window/scale issue; 0 new caps; unit merits source review. |
| USD building_permits | 6 | Short-window/scale issue; 0 new caps; unit merits source review. |
| USD redbook_yoy | 6 | COVID/reopening fat tails; 1 new cap. |
| AUD retail_sales_mom | 5 | COVID/reopening fat tails; 2 new caps. |
| CAD manufacturing_sales_mom | 5 | COVID/reopening fat tails; 3 new caps. |

## Score sanity and local rebuild

- CHF old +2.008 → new **+1.884**: producer/import prices YoY signal +1.736 × weight 3 = +5.208; headline CPI +1.348 ×3 = +4.043; retail sales YoY +0.888 ×2 = +1.776. Inflation +1.542 and growth +0.863 dominate.
- NZD old +1.213 → new **+0.759**: business confidence +1.225 ×2 = +2.449; CPI QoQ +1.129 ×2 = +2.259; CPI YoY +0.590 ×3 = +1.769. Inflation +0.805 and growth +0.814 are offset partly by labor −0.027.
- EUR old −0.002 → new **−0.112**: German Ifo +2.533 ×2 = +5.065; ECB inflation expectations −1.165 ×2 = −2.330; manufacturing PMI +1.115 ×2 = +2.229. Growth +1.286 is offset by labor −0.712 and inflation +0.208 under ECB single-mandate weighting.

| Currency | As of | Old score | New score |
|---|---|---:|---:|
| AUD | 2026-10-01 | -0.519 | -0.639 |
| CAD | 2026-10-01 | -0.719 | -0.175 |
| CHF | 2026-10-01 | +2.008 | +1.884 |
| EUR | 2026-10-02 | -0.002 | -0.112 |
| GBP | 2026-10-01 | -0.579 | -0.641 |
| JPY | 2026-10-01 | -0.259 | +0.767 |
| NZD | 2026-09-30 | +1.213 | +0.759 |
| USD | 2026-10-02 | -0.996 | -1.394 |

- Local `job:macro_state` five stages all succeeded. Focused tests: 5 passed. Full suite: 527 passed, 7 skipped, 1 existing warning. Production was not touched.
