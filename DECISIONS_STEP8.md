Read AGENTS.md, PROGRESS.md, REPORT_STEP7.md first. Save this prompt
verbatim to DECISIONS_STEP8.md. Sub-tasks in PROGRESS.md; commit per part.
Scope: Fed only (ECB comes with the EUR desk in step 9).

PART 0 — STEP 7 FOLLOW-UPS
- Redact secrets in logs for every HTTP client (EODHD, FRED, OpenAI,
  others): strip api_token/api_key query params and auth headers before
  logging. Test that a logged error URL contains no key.
- Untrack checkpoint files (.backfill_progress.json and similar), add them
  to .gitignore.
- FED current rate: the page must take the current policy rate from data
  (latest decision in the DB / rate service), not cb_meetings.yaml. Config
  value becomes a fallback only, shown with its as-of date if used.
- Rebuild after the Part F backfill: run scripts.rebuild_event_innovation
  --dry-run --key-diff, then the real run; then the Macro State chain.
  Report rows changed.
- Unmapped events: list the top 40 G10 unmapped event names by count
  (report only, no mapping changes).

PART A — PER-CB CONFIG: config/central_banks.yaml
For all 8 banks: mandate (dual / single price stability), inflation target,
projection publication (name, cadence), rate path type (dots / none /
own_track), conditioning (own_views / market_path / market_curve /
board_median / constant_rate), voting structure, meeting + minutes source.
Fill the Fed fully; others from public knowledge, marked verify: true.

PART B — FED PROJECTIONS (deterministic parsing, NO LLM)
- Source: the Fed's HTML Summary of Economic Projections pages
  (fomcprojtabl<YYYYMMDD>.htm) and their accessible data tables for the
  dot plot and the uncertainty/risk figures. If a part is only in the PDF,
  report which and skip it.
- Migration 0024:
  cb_projection_values(bank, release_date, variable, horizon [2026|2027|
    2028|longer_run], stat [median|ct_low|ct_high|range_low|range_high],
    value) PK all but value;
  cb_dots(bank, release_date, horizon, rate, participants);
  cb_risk_balance(bank, release_date, variable, kind [uncertainty|risk],
    lower_or_downside, similar_or_balanced, higher_or_upside);
  cb_projection_errors(bank, publication_year, variable, horizon, rmse)
    from the SEP's Table 2.
- Backfill every SEP round from 2020 onwards.
- Validation (reject the round and log if any fails): median within range,
  central tendency within range, dots per horizon = participant count,
  values within plausible bounds per variable.
- Verify against the March 2026 SEP: 2026 medians GDP 2.4, UR 4.4, PCE 2.7,
  core PCE 2.7, funds rate 3.4; December 2025 PCE 2026 = 2.4.

PART C — CB TRACKING (Fed)
- Price data: FRED PCEPI and PCEPILFE (index levels), UNRATE, GDPC1 (FRED
  key exists). Store in the existing FRED pattern.
- For each 2026 projection: required pace for the rest of the year to hit
  the Q4/Q4 projection given data so far; latest 3-month annualised rate;
  status:
    inflation: actual − required > +0.5pp → running_hot, < −0.5pp →
      running_cold, else on_track;
    unemployment: latest 3m avg vs Q4 projection, ±0.2pp;
    GDP: latest quarter annualised vs required, ±1.0pp.
  Also return the 70% band from cb_projection_errors.
- Revisions: latest round vs previous round per variable/horizon (median,
  and width change of CT and range).
- Reaction-function flag: inflation 2026 median revised up ≥ 0.2pp AND
  funds-rate 2026 median unchanged → "tolerance"; both up → "response".

PART D — FED REGIME MODEL v1 + GAP
- Current regime from policy-rate history: hiking (last move up within
  6 months), cutting (last move down within 6 months), holding, near_zero
  (rate ≤ 0.5%). QE needs balance-sheet data → "unavailable" for now.
- Transition checklists, each condition computed from data or marked
  unavailable with a reason:
  Hiking→Holding: core PCE 3m ann < 3%; labor surprises negative 8 weeks
    (Event Innovation); financial conditions tightening (FRED BAMLH0A0HYM2
    up > 50bp in 3 months); "policy restrictive by majority" → unavailable
    (needs speaker pipeline).
  Toward cutting/QE: Sahm rule triggered (compute from UNRATE); ISM
    manufacturing < 50 for 3+ months; HY credit spread > 600bp; core PCE
    3m ann below 2%; policy rate ≤ 0.5%.
- Gap v1 (Fed, own-path bank): SEP median end-year path vs market-implied
  rate at the same dates (from the Part D futures engine). Gap in bp per
  horizon. Add a tilt flag from CB Tracking (running_hot → hawkish risk,
  running_cold → dovish risk). Label the method in the API output.

PART E — APIs AND DESK PANELS
- GET /api/cb/{bank}/projections?round=latest|all, /dots, /risk-balance,
  /tracking, /regime, /gap.
- Fill USD desk panels: 2 Direction "vs Fed SEP" column; 3 Fed view
  projections block (revisions + reaction-function flag + risk balance);
  4 Fed path (regime ladder + transition checklists); 6 Gap (dots vs market
  chart + gap). Match design/design-mockups/Main.dc.html and
  CentralBanks.dc.html (Projections view) for layout.

TESTS
- SEP parser on a saved March 2026 HTML fixture (values above).
- Validation rejects a corrupted round.
- Required-pace maths; status thresholds; Sahm rule; regime classification.
- Gap uses the same dates for dots and market path.
- Log redaction test. Full suite with nothing deselected.

DO NOT: ECB or other banks' projections (config only); use an LLM for
projection extraction; new Python dependencies; change Event Innovation /
Macro State maths.
DELIVER: REPORT_STEP8.md (≤60 lines): Part 0 rebuild counts, SEP rounds
loaded, validation failures, tracking statuses, regime + gap output,
panels filled, tests.
