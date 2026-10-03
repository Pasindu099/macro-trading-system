# Step 6 — USD desk

## Part 0: Step 5 follow-ups

- USD/SEK was added to FX ingestion (`fx_spot.py`; it is a DXY component and not a G10 pair). The backfill 2010-01-01 → 2026-10-03 used 1 history call and 1 listing call, and inserted 4,443 rows.
- `app/services/dxy.py` computes ICE DXY from the six components on common dates. It is stored in `fx_spot_observations` as pair `USD/DXY`, `source_type='computed_dxy'`, and is rebuilt inside `job:rates_derived` after flagging. That gives 4,428 rows, 2010-01-01 → 2026-10-02. Check: 77.46 (2010-01-04) and 113.96 (2022-09-27). The outlier flagger skips the computed series.
- Positioning USD spot is now the computed DXY; the equal-weight index was removed. USD squeeze: LF `none`, AM `trend_confirming` (2W +1.77%). USD extremes (episodes / avg 8W % / against-crowd % / max adverse %): >90 13/−0.51/69.2/2.54; 85–90 17/−0.16/56.2/1.85; 10–15 20/+0.59/60.0/1.75; <10 19/+0.64/68.4/2.47.
- API: `pct_reversed_8w` → `against_crowd_after_8w`. New `max_adverse_move_8w` is the largest daily-close move against the crowd within 8W, averaged per band. The 90% coverage rule is unchanged.
- Tests: DXY formula and component completeness, adverse move, renamed field. The Step 4 pair-count test now expects 28 G10 pairs + USD/SEK. Full suite: 362 passed, 7 skipped, 1 deselected.

## Part A: desk framework

- `/desks/{currency}` comes from `config/desks.yaml`. Only USD is enabled; EUR and the others return 404 and appear disabled in the desk nav. The spec is `design/design-mockups/Main.dc.html`; that is the actual folder, not `design/mockups/`.
- Jinja page `app/web/templates/desk/page.html` + 15 partials at `/desks/USD/panels/{id}`. They load lazily with `hx-trigger="revealed"`, and ECharts initialises from `data-chart` JSON (`app/web/static/js/desk.js`). HTMX is loaded from unpkg the same way ECharts already was; there are no Python dependencies. Mockup tokens are scoped under `.desk` (`desk.css`), so the existing shell is unchanged.
- **Unmapped indicators** (`scripts/check_desk_indicators.py`: 17 checked, 4 not in DB). They render as unavailable and are not substituted: real GDP QoQ annualised (no US GDP indicator at all), ISM manufacturing production, ISM services prices paid, ISM services new orders.

## Part B: panels

| Panel | State | Source |
| --- | --- | --- |
| Verdict | Live regime label (1M, 2Y vs 10Y); bias/conviction/thesis → step 10 | `curve_metrics.get_curve` |
| Situations | → step 10 | — |
| 0 Price | Live: computed DXY, 200-day avg, 1M/3M/6M/1Y; USD vs 7 majors 1W/1M/3M | `fx_spot_observations` |
| 1 Economy | Live: inflation/labor/growth z + mandate-weighted composite (`cb_strength_score`) | `macro_state.get_macro_state_board` |
| 2 Direction | Live: avg surprise + decayed live σ per theme, 30d; vs Fed SEP → step 8 | `build_event_innovation_feed` |
| Key data | Live: 6 expandable cards, latest/prior/change/12m-ago; GDP card unavailable; oil driver → step 10 | `indicator_releases` |
| 3 Fed view | **Source unavailable**: `cb_policy_documents` has 0 rows; projections + speakers → step 8 | `get_cb_policy_data` |
| 4 Fed path | → step 8 | — |
| 5 Priced | Live: next 3 meetings (cut/hold/hike, implied), 2Y vs Fed funds 24m | `get_rate_probability_view` (yfinance_ZQ) |
| 5b Curve | Live: regime 1W/1M/3M, 2s10s, 10s30s, 2Y − policy, un-inversion, curve and 2s10s charts, 4 checks; real 10Y unavailable (no breakevens) | `get_curve` + yields |
| 6 Gap | → step 8 | — |
| 7 Positioning | Live: LF/AM 3y percentile, crowding, squeeze; link to /positioning | `positioning.py` |
| 8 Catalysts | Live: next 10 US events, impact from importance (1 = high) | `list_calendar_events` |
| News | Live: USD `news_alerts`, filtered by `implied_tier` (latest stored item: 24 Sep) | `intelligence.news_alerts` |
| 9 Scenarios | → step 10 | — |

- Not rendered because no source exists: mockup support/resistance lines, "2Y spread vs G10 avg", catalyst weighting by Fed focus, and news price reaction. Retail positioning row: out of scope for this step.

## Part C: quality

- Each panel has loading (placeholder), empty, unavailable, pending and error states. Errors are logged and rendered as a short message with a Retry button; they are not cached. Rendered panels are cached in-process for 60 s, keyed by currency, panel and parameters.
- 390 px: at a true 390 px width (375 px viewport), `scrollWidth` = `clientWidth`, and 0 elements overflow outside `.table-wrap`. Headless Chrome won't render narrower than 526 px, so this was measured in a 390 px same-origin iframe.
- Screenshots (headless Chrome, temporary auth-off preview container, since removed): `data/screenshots/usd_desk_desktop.png` (1440 px) and `data/screenshots/usd_desk_390.png`.
- Fixes from the visual check: curve tiles read `value_bp`; catalyst impact uses 1 = high; the reference line is kept inside the chart's y-range.

## Tests

- `test_desks.py` (6): USD only, page order and loading states, EUR 404, validation reports unknown names, 60 s cache, errors not cached. `test_desk_panels.py` (36): each panel returns 200 with data and 200 with an empty/unavailable/pending state when its source returns nothing. It also checks that pending panels hold no numbers, missing series aren't substituted, interactions, the USD sign convention, curve tiles and catalyst impact.
- Final full suite: **404 passed, 7 skipped, 1 deselected** (known FED test). Integration: 25 passed.
