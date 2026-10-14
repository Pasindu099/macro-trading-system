# DECISIONS — STEP 11: CALENDAR + EVENT PANEL + EVENT LOG

Spec: design/design-mockups/Calendar.dc.html. Rules: AGENTS.md; in-container commands; one part per commit; focused tests; full suite once per part. No LLM except the Event Log button in Part E. Long jobs (rebuilds, backfills): run detached, poll at most every 3 minutes.

## Status
Parts A–C done and verified (e3fafcb, 7eab218, 9e63a46, 19a80a9). Remaining: Part A follow-up (CN trade mapping), then Parts D, E, F.

## PART A — CALENDAR DATA SERVICE (done)
calendar_service.list_events(start, end, currencies, min_impact): any date range, past or future. One row per release (release_bundles, fallback same country + release time). Impact = EODHD importance + config/calendar_overrides.yaml.
China (CN) is calendar-only (tag affects AUD, NZD); never in Macro State or verdicts.

### Part A follow-up (small, do first)
Map the most common unmapped CN events: Exports, Imports, Balance of Trade (Yuan and USD variants) → one "China trade balance" release bundle with exports/imports as components. Re-run CN ingestion; report mapped/unmapped. Remain calendar-only (regression tests from 19a80a9 must still pass).

## PART B — FAST ACTUALS (done)
High-impact events polled every 60 s for 10 min after release; status scheduled → confirmed; Event Innovation incremental on confirmation.

## PART C — CALENDAR LIST (done)
Default today; presets; custom past/future ranges; filters incl. surprise > 1σ; day groups with sticky headers; UTC/local toggle; progressive loading.

## PART D — EVENT DETAIL PANEL (right side)
- Opens when a row is clicked (HTMX partial); < 1024 px opens as a full-screen sheet with a close button.
- Header: actual / consensus / previous (+ "revised from") / surprise σ, and source status. "Why it matters now": reuse the Overview key-events template (CB mandate + tracking status). Calendar-only (CN): "Affects AUD, NZD via China demand" (template), no CB tracking text.
- History tab: last 24 releases of the headline indicator, actual vs consensus (bars + consensus line, ECharts); toggle "first print vs revised" using stored release versions (first stored actual vs latest); surprise-σ bars below. Upcoming events: history + consensus, latest slot empty.
- Components tab: bundle members with actual / consensus / previous / surprise each. Single-indicator releases say so.
- Reactions tab, labelled "Release-day move (daily close, not intraday)":
  - Related pairs: the currency's USD pair + its 2 most-traded crosses (config); CN events → AUD/USD and NZD/USD.
  - Per past release: close-to-close move on the release date in each pair (positive = event currency up).
  - Stats over all history: average |move|, average signed move when |surprise| > 1σ (split beat / miss), hit rate = share of releases where sign of surprise matched currency move (only |surprise| ≥ 0.5σ).
  - Nightly job job:event_reaction_stats (existing job framework) writes event_reaction_stats (migration). Show sample size; hide stats with < 8 releases ("too few releases").
  - Manual Event Log reactions for this event underneath, if any.
- Links: currency desk (only if enabled), related pairs, indicator history on the Data page, "Log to Event Log" (opens Part E form prefilled).

## PART E — EVENT LOG PAGE (/calendar/event-log)
- List with filters: currency, date range, event type, traded yes/no. Newest first.
- Create / edit form: linked calendar event (search picker) or free-text event, date, pairs (multi), expectation before, outcome after, notes, traded (yes/no), direction (long/short), entry, exit, size, P&L in pips and amount — all trade fields optional.
- Release-day price reactions auto-filled for linked events (from Part D data).
- AI interpretation ONLY when the user clicks "Generate AI note" (OPENAI_MODEL). Migration: ai_note_raw and ai_note_final stored separately; editing changes only ai_note_final; raw never overwritten. If OpenAI fails, show the error and keep the form usable.
- Existing event-log admin API keeps working (adapt, don't break). Migrate existing ai_interpretation values into both raw and final.
- Delete requires confirmation.

## PART F — VISUALS (all calendar pages)
- Shared top nav and freshness indicator; fonts via CSS variables.
- Colours: green/red + ▲▼ for price moves (reactions); amber/blue for surprises (beat = amber, miss = blue) — a beat is hawkish, not bullish.
- Responsive to 390 px; detail panel as full-screen sheet on small screens.

## TESTS
- CN trade bundle mapping; CN still excluded from Macro State.
- Detail panel: header fields, first-print vs revised toggle data, upcoming event with empty latest slot, components list.
- Reaction stats maths: signed move per release, hit-rate rule (≥ 0.5σ only), beat/miss split, minimum 8 releases.
- Event Log: create/edit/delete; optional trade fields; raw vs final AI note (edit never changes raw); OpenAI failure handled; admin API unchanged.
- Full suite green in-container.

## DELIVER
Update REPORT_STEP11.md (≤60 lines), commit it, and give a ≤10-line status.
