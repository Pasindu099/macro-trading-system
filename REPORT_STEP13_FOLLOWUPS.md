# Step 13 follow-ups — 19 October 2026

## 1. Headline parser coverage (`app/services/news_actuals.py`)

Last 30 days, local: 91 investingLive items tagged Data (85 unparsed + duplicates of 5 parsed in the brief).

| Format group | Count | Handling |
|---|---:|---|
| "X vs Y expected/estimate" with unknown indicator, K/M units, "versus" | 27 | parsed |
| Parenthesised "X (expected Y, prior Z)" | 6 | parsed |
| Finals "X vs Y prelim" / "X vs Y prior" (no consensus) | 10 | parsed; prelim used only to pick the variant, never stored as consensus |
| Narrative "INDICATOR eases to / confirmed at / jumps to N" (first clause, one number) | 12 | parsed |
| PBOC yuan fixings "(vs. estimate …)" | 14 | excluded: not a calendar release |
| Wraps, calendars, previews ("seen at"), GDPNow, Dallas trimmed mean, "firms expect" | 10 | excluded |
| No number ("beats estimates", "build vs draw", "touches low", …) | 6 | not parseable |
| "S&P global composite PMI … versus preliminary" (no country) | 1 | left unparsed (country ambiguous) |

Fixes on the way: euro area mapped to `EZ` but the calendar uses `EU`; US payrolls mapped to `nonfarm_payrolls%` (France's series) instead of `nfp`. Matching now uses exact canonical names + comparison suffix (`= ANY(names)`) instead of `LIKE prefix%`, so e.g. headline durable goods can never match `durable_goods_ex_transport_mom`.

| | Before | After |
|---|---:|---:|
| Parsed / all 91 Data headlines | 5 (5.5%) | 60 (66%) |
| Parsed / 61 headlines that carry a release print | 5 (8%) | 60 (98%) |
| Matched to a calendar release | 5 | 28 |
| Matches that disagree with the EODHD actual | 0 | 0 |
| Unmatched (indicator not in calendar, e.g. JOLTS, NAHB, durable goods, CN PMIs; or narrative posted > 30 min after release) | – | 32 |

- 70% target: met on release-print headlines (98%), not on all 91 (66%) because 30 tagged "Data" items are not releases (14 PBOC fixings, wraps, previews). Tagging PBOC fixings as Flows & market instead of Data would make the denominator honest; not changed (owner's call).
- Zero wrong matches: all 28 matched values equal the stored EODHD actual.
- Re-run: deleted only the 86 `unparsed`/`unmatched` audit rows for the window and reprocessed; no newswire actual was written (all matched releases already had EODHD actuals).
- Tests: `tests/unit/test_news_actuals.py` 19 passed. Full suite 741 passed, 7 skipped.

## 2. S&P 500 history (`app/services/news_assets.py`)

- EODHD `GSPC.INDX` is now the primary source (fetched with the metals); FRED `SP500` is still written first and survives only on dates EODHD does not return or when the EODHD call fails (logged, other symbols continue).
- Local coverage after refresh: SP500 2000-01-03..2026-10-19, 6,739 rows, all `source=eodhd` (was FRED 2016-10-17.., 2,514 rows). Cost: 3 EODHD calls.
- Tests: `tests/unit/test_news_assets.py` 4 passed. VPS: picked up by the next deploy + `refresh_snapshot()` (daily job refreshes 7 days only, so run the backfill once there).

## 3. Non-releases retagged (Step 14 decision 2)

- Data type now requires a release print (`app/services/news_tagging.py` `_release_like`): items matching the parser's non-release pattern (PBOC fixings, wraps, calendars, previews, "seen at", "expects", GDPNow) or whose first clause is not about a data release become **Flows & market**.
- Retag of all stored Data items: 53 moved to Flows & market, 148 stay Data. Last 30 days: Data 63, Flows & market 28 (14 PBOC fixings among them).
- Parser on real releases (last 30 days, Data only): 58 of 63 parsed (92%), 28 matched, 0 EODHD disagreements. Unparsed: China Q3 GDP "beats estimates" ×2 and the private oil-inventory survey (no number), S&P Global composite PMI (no country), "Sticky core inflation pushes RBA…" (commentary with a data keyword).
