# P0 Macro State correction — local only

## Source and ingestion findings

- EODHD's October 4 backfill returned three Japanese calendar events with `actual` already filled for October 22/29. The backfill passed them through `IngestService` without a release-time check (`scripts/backfill_calendar_gaps.py:53,121`; `app/ingestion/ingest_service.py:125-145` before fix). Their retrieval time was Oct 4 13:40 UTC. No source row was deleted.
- EODHD also uses one event label for two valid EU series. The [ECB's August survey](https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.pr260918~295b3ab978.ga.html) reported a **3.0% median 12-month inflation expectation** on Sep 18. The [European Commission's September survey](https://economy-finance.ec.europa.eu/document/download/67757055-585f-4928-a381-35f3f0dd5b97_en?filename=bcs_2026_09_statistical_annex_en.pdf) reported **35.2 points of balance** for expected price trends over the next 12 months on Sep 29. Its [2023 annex](https://economy-finance.ec.europa.eu/document/download/cdeaacc1-47a1-443a-98e3-9e0bd2930c47_en?filename=bcs_2024_06_statistical_annex_en_0.pdf) also confirms 4.8/9.0 point balances; numeric threshold alone would misclassify them. EODHD payloads lack source/unit fields.
- Eurozone headline HICP Sep 2026 **3.8%**, prior **3.2%**, is verified by [Eurostat](https://ec.europa.eu/eurostat/en/web/products-euro-indicators/w/2-02102026-ap). Its pre-fix standardized surprise **4.360333** reflects a genuine 0.6 percentage-point jump against a roughly 0.179-point prior rolling standard deviation. It is retained; the 0.5-unit floor reduces its normalized amplitude in the rebuild.

## Fixes

- Ingestion now stores premature `actual` only in the raw audit payload, sets the column to NULL, and flags `quarantine_reason=future_actual_before_release`. The local repair quarantined **3** existing Japanese rows. Scheduled calendar rows remain.
- The processed dataset excludes future releases and quarantined rows before building observations/features. The board selects each currency's latest score no later than its own latest observed feature date and today.
- EU expectations are split into ECB percent (`consumer_inflation_expectation`) and Commission survey balance (`consumer_price_expectations_balance`). Current-month reference periods and source-consistent magnitude/estimate identify balances, including delayed year-end releases. The local repair moved **85** rows and corrected **15** revision `is_latest` flags; both definitions have separate unit metadata. Classification is inferred because EODHD does not expose a source tag.
- Standardized surprises use a minimum prior standard deviation of **0.5 native units** or **5% of the prior mean**, whichever is larger. This stops tiny variation in rounded percent prints from creating huge z-scores while scaling the floor for level indices. Values above ±5 are capped, with indicator/date/raw z logged per capped feature (**334** in the final local build); CB scoring applies the same defensive cap.
- The full eight-currency scan and unfixed findings are in [REPORT_P0_SCAN.md](REPORT_P0_SCAN.md). No other indicator was repaired.

## Before / after (local DB)

| Currency | Before date | Before score | Before inflation | After date | After score | After inflation |
|---|---|---:|---:|---|---:|---:|
| AUD | 2026-10-29 | -0.465287 | +0.613572 | 2026-10-01 | -0.519066 | +0.134901 |
| CAD | 2026-10-29 | -0.362924 | +0.423108 | 2026-10-01 | -0.719231 | +0.306880 |
| CHF | 2026-10-29 | +0.183313 | +3.320978 | 2026-10-01 | +2.008245 | +1.820000 |
| EUR | 2026-10-29 | +2.345231 | +18.615813 | 2026-10-02 | -0.001925 | +0.414849 |
| GBP | 2026-10-29 | -0.374483 | +0.065185 | 2026-10-01 | -0.578613 | +0.016000 |
| JPY | 2026-10-29 | +0.039758 | +3.053498 | 2026-10-01 | -0.259247 | +0.199012 |
| NZD | 2026-10-29 | -0.534787 | unavailable | 2026-09-30 | +1.213358 | +2.040000 |
| USD | 2026-10-29 | -0.830822 | -3.607987 | 2026-10-02 | -0.996495 | -0.590000 |

Final local Macro State chain: all five stages `success`; processed features 26,578 rows, max release date Oct 2, max absolute standardized surprise 5. Local `indicator_releases` retains three flagged rows with NULL actual. Focused tests: 31 passed; full suite: **526 passed, 7 skipped, 1 existing warning**, nothing deselected. Local GET `/`, `/desks`, `/desks/USD`, `/desks/EUR`: 200, no Oct 29 date. Production remains at `dd82046` and was not changed, restarted or rebuilt.
