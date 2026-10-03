Read AGENTS.md, PROGRESS.md, REPORT_STEP5.md first. Save this prompt
verbatim to DECISIONS_STEP6.md. Add sub-tasks to PROGRESS.md; commit per part.

PART 0 — STEP 5 FOLLOW-UPS
- USD spot for positioning: replace the equal-weighted index with the ICE
  US Dollar Index computed from components:
  DXY = 50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × GBPUSD^-0.119
        × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036
  Add USD/SEK to FX ingestion + backfill to 2010 (one request). Store the
  computed series (source="computed_dxy"). Recompute USD squeeze/extremes.
- Rename "reversed within 8W" to "against_crowd_after_8w" in the API, and
  add "max_adverse_move_8w" (largest move against the crowd at any point
  in the 8 weeks, average per band).
- Keep the 90% lookback coverage rule.

PART A — DESK FRAMEWORK
- Visual spec: design/mockups/Main.dc.html (markup + the data in its
  script). Match its layout, sections, tokens and states. Ignore its
  generated/illustrative numbers.
- Stack: Jinja + HTMX + ECharts (no new dependencies). Each panel is a
  server-rendered partial loaded lazily with hx-get; charts initialise from
  a JSON data attribute.
- One generic desk route /desks/{currency} driven by config/desks.yaml.
  Only USD is enabled in this step; other currencies return 404.
- config/desks.yaml (USD): CB = Fed, curve country = US, key indicators
  mapped to canonical names in the indicators table: CPI YoY, core CPI YoY,
  core PCE YoY, unemployment rate, real GDP QoQ annualised, ISM
  manufacturing + sub-indices, ISM services + sub-indices, nonfarm payrolls.
  If an indicator or ISM sub-index is not in the DB, list it in the report
  and render that series as unavailable — do not substitute.

PART B — PANELS (wire existing services; each panel loads independently)
Build every section from the mockup, in this order. Panels whose data
does not exist yet render a clear "Available after step N" state with no
placeholder numbers.
  0 Price: DXY (computed) + USD vs G10 % change table (1W/1M/3M), 200-day
    average; range buttons 1M/3M/6M/1Y.
  1 Economy: macro state theme z-scores for USD (inflation, labor, growth)
    + mandate-weighted composite from the existing Macro State service.
  2 Direction: Event Innovation surprises per theme, last 30 days.
    "vs Fed SEP" column → "Available after step 8".
  Key data at a glance: the indicator charts from desks.yaml, expandable,
    showing latest/prior/change/12m-ago from indicator release history.
    The oil "inflation driver" panel and situations → "after step 10".
  3 Fed view: latest CB policy documents + tone from the existing CB policy
    service; projections block → "after step 8".
  4 Fed path (regime ladder, transitions) → "after step 8".
  5 Priced: rate probability service (own engine) for the next 3 meetings
    + 2Y vs Fed funds chart from rates services.
  5b Rates & curve: curve_metrics (regime 1W/1M/3M, 2s10s, 10s30s,
    2Y − policy, un-inversion) + yield curve chart.
  6 Gap → "after step 8".
  7 Positioning: positioning service (leveraged funds + asset managers
    percentile, crowding, squeeze status) with a link to /positioning.
  8 Catalysts: next 10 USD calendar events (existing calendar data).
  News: USD items from the news service with type filter.
  9 Scenarios → "after step 10".
  Verdict header: currency, CB, current regime label from curve service;
  bias/conviction → "after step 10".

PART C — QUALITY
- Every panel handles: loading, empty data, unavailable source, error
  (logged, panel shows a short message; page never fails as a whole).
- Panel responses cached 60 s server-side.
- Responsive down to 390 px wide (stack sections); no horizontal page scroll.

TESTS / CHECKPOINTS
- Each panel endpoint: 200 with data, 200 with unavailable state when its
  service returns nothing.
- desks.yaml validation: unknown canonical names reported, not crashing.
- /desks/USD renders; /desks/EUR returns 404 for now.
- Full suite once per part (known FED test deselected).

DO NOT: build EUR or other desks; invent numbers for pending panels; add
dependencies; change backend calculations except Part 0.
DELIVER: REPORT_STEP6.md (≤60 lines): panels live vs pending, unmapped
indicators, screenshots taken with the browser tool if available
(desktop + 390 px), test results.
