# Dashboard review — 14 November 2026

Status: local implementation partially complete; stopped under the two-failure rule. Not deployed or committed.

## Data findings
- Docker services restarted after app startup failed while PostgreSQL was starting.
- FX/yields newest observation: 12 Nov; FRED: 11 Nov; Macro State: 12 Nov. The reported 17-day Macro State gap is no longer present locally.
- Retail refreshed: 28 pairs stored; latest snapshot 14 Nov.
- COT recovery fetched 120 rows, wrote 0; provider still returns 3 Nov rather than the expected 10 Nov report. Remains stale.
- News feeds responded successfully; latest stored investingLive article advanced to 13 Nov 22:02 UTC but remains beyond the six-hour limit.
- ECB/RBNZ pricing sources remain stale; some central-bank sources require manual imports.
- Startup source/derived catch-up was running at the final audit; its completion is not verified.
- USD macro score was -0.595 while December Fed pricing implied a hike. Different inputs/horizons explain the disagreement; prices were not used to force a bullish verdict.

## Implemented
- Fractional freshness limits preserved, ages rounded: `app/services/freshness.py:157`.
- Stale macro/curve/situation/COT contributions excluded; affected verdict confidence reduced: `app/services/verdict.py:225`.
- Restart recovery checks successful calendar ingestion, rather than failed attempts: `app/ingestion/scheduler.py:571`.
- Simplified navigation; implemented currencies only; Central Banks under Currencies; Positioning/Sources under More: `app/web/templates/base.html:23`.
- One shared stale banner; repeated panel strips removed. Positioning freshness guard retained: `app/web/templates/base.html:49`, `app/api/routes/positioning_page.py:93`.
- Desk summary uses view/reason/risk; cards fill the grid; panels request immediately; pending panels disappear: `app/api/routes/desks.py:73`.
- Scenarios have distinct hold/hike/cut descriptions and label percentages as meeting-outcome pricing.
- Pairs defaults to five columns, includes an Advanced control and promotes institutions/retail disagreement: `app/web/templates/pairs/page.html:8`.
- Pairs price-history query bounded to 45 days; independent policy-model reads run concurrently: `app/services/pairs_service.py:51`.
- General-interest news filtered from new and historical feed reads; explicit official rate guidance upgraded; default feed shows High/Medium: `app/services/news_tagging.py:240`, `app/services/news.py:215`.
- Calendar defaults to next seven days and browser-local time; matching duplicates grouped; secondary releases downgraded: `app/api/routes/calendar.py:62`.
- Statement initials protected, including PDF line wraps; upcoming CB event entities decoded: `app/services/cb_statement_diff.py:44`.
- Unfinished CB tone/search notices removed; policy cycle distinguished from latest decision. Positioning stale explanations reduced; adding/cutting bar colours aligned; explainer backgrounds softened.

## Verification
- Focused runs passed: 37, 43, 75 and 19 tests respectively (overlapping selections; not additive).
- Later combined regression run: 62 passed before the Positioning missing-OI failure.
- Live smoke: 14 sampled pages/panels returned HTTP 200 without sampled error markers. Checked USD panels: 0.19–1.16 seconds.
- Pairs cold response improved from 11.54 to 8.20 seconds in separate live smoke runs; remains relatively slow.
- JavaScript syntax and whitespace checks passed before the final follow-up edits.
- Full suite run once: **968 passed, 29 failed, 8 skipped**. Several navigation/placeholder expectations were subsequently updated and passed in focused checks; no second full run.
- Rates/research integration selection subsequently passed: 16 tests.

## Stop / remaining work
- `tests/unit/test_positioning_step18.py::test_small_short_vs_heavy_short_wording_and_missing_oi` still returns `stale` where `unavailable` is expected. Failed twice after isolation; stopped instead of further variations.
- Full-run failures also included BoJ tracking and Overview active/watch grouping; these remain unresolved.
- Positioning integration found a missing `rows` variable after strip removal. Restored the guard, but its final integration and expanded smoke checks remain unverified.
- Browser host unavailable: desktop/mobile visual QA and interactive toggle checks remain unverified.
- Whole-app plain-English/summary/theme coverage and remaining Overview placeholders still need a final pass.
- Existing unrelated working-tree changes were preserved; no production deployment performed.
