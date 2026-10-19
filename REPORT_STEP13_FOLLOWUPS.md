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
