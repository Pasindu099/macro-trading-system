# Step 18 - Takeaway framework and Positioning (2026-10-29)

| Part | Delivered | Verification |
|---|---|---|
| A | Shared macro/CSS, deterministic registry, glossary, Explanations cookie switch | Focused 40 passed/1 skipped; full 929 passed/8 skipped/1 warning (140.17s). |
| B | Positioning data, dollar/interest views, v2 squeeze/extremes, separate Legacy ingestion | Focused 96 passed; full 960 passed/8 skipped/1 warning (106.12s). |

- Supplied decisions saved in `DECISIONS_STEP18_TAKEAWAYS_POSITIONING.md` and `DECISIONS_THEME_LIGHT.md`; both supplied mockups copied to `design/design-mockups/`.
- `app/services/takeaways/`: one function for each of seven Positioning panels; three-part contract with explicit unavailable/stale text. Live readings and per-panel placement remain Part C.
- `_takeaway.html`, `takeaways.css`, `config/glossary.yaml`: escaped text, accessible native details/summary glossary, longest-term matching; coverage and writing-rule tests.
- `explanations.js` and base header: On by default, cookie remembered; Off hides marked explanations while retaining numbers/charts. Existing long summary helper marked for hiding.
- Browser: 1440/390, actual Chromium keyboard/touch definitions and switch/cookie/reload passed (`tests/browser/check_takeaways.cjs`). Node-only unit test skipped in the Python container; browser check covers the real JS.
- B: `positioning_values.py` provides report-date dollars, both TFF groups for all 28 pairs, single-market %OI, USD-base sign/long-short flips, cross-$bn fallback, net/long/short/price/OI and 20th/80th range lines; no DX subtraction from USD pairs.
- Dollar conversion uses the existing dated valid close on/before the report date (maximum five days old); future or missing/stale quotes never manufacture a value. Cross price charts use the stored pair, not a synthetic ratio.
- `/api/positioning/pair/{pair}?weeks=156` preserves the ranking score and adds fast/slow $bn histories; `/simple` and `/history/{instrument}` expose data for Part D. Pair screener shows $bn + implied labels/as-of dates next to its existing score.
- `positioning_step18.py`: side/extreme thresholds, sorted simple/bar contexts, adding/cutting relative to bet sign, explicit missing/stale states, three-item squeeze evidence with actual bet/price dates and half-standard-deviation threshold.
- Covered extreme entries count consecutive band weeks once, preserve state across missing percentiles and use calendar 4W/8W outcomes. New comparisons use medians, disclose rises/falls/flat counts and outcome range, and hide metrics below five covered cases.
- Local migration `0050_cot_legacy` adds separate `legacy_futures_only` storage; TFF schema/groups unchanged. Weekly Friday/Monday-retry job registered alongside TFF; idempotent SQL and retry path tested.
- Detached official CFTC backfill: **7,016 rows**, 877 weeks per each of eight contracts, **2010-01-05 to 2026-10-20**, two requests; no TFF rows replaced. Script: `scripts/backfill_cot_legacy.py`.
- Contract sizes verified: [CME FX product guide](https://www.cmegroup.com/markets/fx/fx-product-guide), [ICE DX](https://www.ice.com/products/194/US-Dollar-Index-USDX-Futures); config: `config/cot_contract_specs.yaml`. Legacy name/fields verified against [official CFTC metadata](https://publicreporting.cftc.gov/api/views/6dca-aqww.json).
- Tests: `test_positioning_step18.py` (unit + integration), `test_cot_legacy.py`, affected existing Positioning/screener/scheduler tests. All 28 notional conventions and live Legacy/%OI/pair APIs checked.
- Compatibility adapters update old squeeze labels and the 52-week lookback; redesigned Positioning layouts/toggles and live takeaway wording/placement remain C-D, not silently implemented here.
- STOP after Part B. Parts C-D and E are deferred; no LLM, themes or production deployment changed. Existing unrelated working-tree edits preserved.
