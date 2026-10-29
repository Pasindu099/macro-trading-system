# DECISIONS — STEP 18: TAKEAWAY FRAMEWORK + POSITIONING REDESIGN

Specs: `design/design-mockups/Positioning.dc.html` (dark) and
`design/design-mockups/PositioningLight.dc.html` (light) — replace/add both in
the repo first — plus `DECISIONS_THEME_LIGHT.md` for colours and themes. Rules: AGENTS.md; in-container; one part per commit; focused tests; full
suite once per part. No LLM (templates only).
Sessions: stop after Part B and after Part D with a ≤10-line status.

Goal: anyone, including people who have never read positioning data, can
understand every panel. Positioning is the first page; the same framework will
be rolled out to every other page in later steps.

## PART A — SHARED TAKEAWAY FRAMEWORK (all pages can use it)
- One reusable component (Jinja macro + CSS) rendered at the bottom of each
  panel, with three parts:
  1. **What this shows** — one fixed sentence about the panel itself.
  2. **What it says now** — 1–2 sentences generated from current data.
  3. **Why it matters / what to watch** — one sentence of context.
- Template registry: `app/services/takeaways/` with one function per panel
  returning the three parts (or an explicit "unavailable"/"stale" text).
- Writing rules, enforced by tests where possible:
  - plain words; max 2 sentences per part; numbers in everyday units
    ($bn, %, "this month", "N of M times") — never raw contracts or σ alone;
  - descriptive only: no "will", "likely", "expected to", "forecast";
    allowed: "usually", "in past episodes", "markets expect";
  - if inputs are missing/stale → say so; never guess.
- Glossary: `config/glossary.yaml` (term → one-sentence definition). Terms in
  takeaways and panel labels get a hover/tap definition (accessible:
  focusable, works on touch).
- "Explanations" switch in the top bar (On by default, cookie-remembered):
  Off hides takeaway boxes and long helper text, keeps numbers and charts.
- Coverage test: every panel registered on a page must have a takeaway
  function; the test fails if one is missing. Start with Positioning panels;
  other pages are added to the coverage list as they get takeaways.

## PART B — POSITIONING DATA ADDITIONS
- Lookback options: 52 weeks, 3 years (default), 5 years.
- Per currency: net position as % of open interest, and USD notional
  (net contracts × contract size × price; contract specs in config).
- Section 1 "Where are the big traders betting?" (two views, per mockup):
  - Simple view (default): one card per currency — status (everyone betting
    on / against, they disagree, hedge funds only, slow money only, no strong
    bets), one plain sentence, two bars (fast money = leveraged funds, slow
    money = asset managers; direction + size, full colour when extreme), one
    watch line. Sort: disagreements, then extremes, then one-sided, then rest.
    Side rule: percentile ≥ 65 = betting on, ≤ 35 = against, else none;
    extreme = ≥ 85 or ≤ 15.
  - Bar view: hedge-fund gauge per currency, plain label, $ size, "adding /
    cutting the bet" (relative to the position's sign), watch chip; "Show all
    trader groups" toggle for asset managers + dealers with explanations.
- Squeeze watch v2 (section 3, per mockup):
  - Title "Squeeze watch: who might be forced out?" + one-line explanation of
    what a squeeze is.
  - Rule changes: price "against the crowd" only counts if the 2-week move
    against the crowd is ≥ 0.5 × that currency's typical 2-week move (std of
    2-week returns over the lookback). "Backing out" = net position shrank in
    each of the last 2 weeks (unchanged).
  - Labels: "Squeeze risk: high" (both ingredients), "Squeeze risk: medium"
    (one), "Crowded, but still working for them" (none). No "trend-confirming".
  - Card: currency, label, ONE plain headline sentence for this currency
    (templates per label, e.g. "Hedge funds are heavily betting against NZD
    and the price is moving against them, but they have not started backing
    out yet."), then a 3-item checklist with ✓/✗ and the evidence in brackets:
    Big one-sided bet (heavily short/long, N of 100) · Price moving against
    them (CCY ±x% in 2 weeks, bigger / not bigger than its usual move) ·
    They are backing out (bet changes last 2 weeks: a, b, shrinking / not).
  - Footer: "Bets as of {COT date} (weekly CFTC data) · price as of {date} ·
    price = {CCY vs USD | dollar index for USD}" + desk/pair link.
  - Sort: high, medium, then crowded-still-working; within a label by how
    extreme the bet is.
- Pair-level positioning (all 28 pairs, existing data only):
  - Dollar value per currency per report date = net contracts × contract
    size (config/cot_contract_specs.yaml: EUR 125,000 EUR; GBP 62,500 GBP;
    JPY 12,500,000 JPY; AUD 100,000 AUD; NZD 100,000 NZD; CAD 100,000 CAD;
    CHF 125,000 CHF; DX index × 1,000 USD — verify against CME/ICE specs)
    × the exchange rate on the report date (Tuesday close).
  - USD pairs: use that currency's futures directly; flip the sign when the
    pair is quoted with USD as base (yen futures long = USD/JPY short).
    Do NOT subtract the dollar-index position (DX is ~58% EUR → double
    counting); show DX only as separate "broad dollar" context.
  - Crosses: base-currency dollar value − quote-currency dollar value,
    labelled "implied from {BASE} and {QUOTE} futures".
  - Per pair and trader group (leveraged funds = fast money, asset managers
    = slow money): weekly series in $bn since 2010, plus the existing
    percentile-difference score for ranking.
  - Service function + API GET /api/positioning/pair/{pair}?weeks=156.
  - Positioning page section 4: selector with currencies AND the 28 pairs;
    for a pair, chart fast and slow money in $bn (lines) over the selected
    period with the pair price underneath, and a takeaway, e.g.
    "Hedge funds have cut their EUR/USD shorts for 3 weeks while the price
    rose; long-term investors still hold a large long."
  - Pairs screener positioning column: "$x.xbn long/short" for fast money
    next to the existing lopsidedness score (implied label for crosses).
  - Pair pages (later step) reuse the same service.
- Section 4 "% of open interest" view (per mockup), next to the $bn view:
  - Units switch: "US dollars ($bn)" / "% of open interest". The % view is
    available for single currencies and USD pairs only (one futures market);
    for crosses show the explanation and fall back to $bn.
  - Series toggles: net, long, short, price (right axis), open interest,
    top/bottom 20% lines (20th/80th percentile of net over the range).
  - Trader groups: Hedge funds (leveraged funds), Long-term investors
    (asset managers), and "Large speculators (as on most sites)" = Legacy
    report non-commercials. Ingest the CFTC Legacy futures-only report for
    the same 8 contracts (free, same source as TFF), backfill to 2010, weekly
    job alongside TFF; store as its own report type.
  - Ranges: 1Y, 3Y, 5Y, since 2010.
  - Takeaway (% view): "{Group} are net {long/short} {X} by N% of all open
    contracts. That is in the bottom/top 20% … / within the normal range
    over the last {range}." — wording must handle "rarely bet more heavily"
    vs "bet has rarely been this small" depending on the sign.
  - USD-base pairs flip sign and swap long/short (yen futures long = USD/JPY
    short).
- Section 5 v2 "What happened the last times they were this extreme?"
  (per mockup):
  - Currency chips (same selection as section 1).
  - If hedge funds are at an extreme today (≤ 15 or ≥ 85): one plain
    sentence — "Hedge funds were this short JPY 9 times since 2010. In 5 of
    those, the yen rose over the next 8 weeks (typical rise +3.9%); in 4, it
    fell (worst −2.7%)." "Typical" = median.
  - One mark per past episode (episode = entry into the same band after
    being outside it; consecutive weeks count once; existing coverage rule):
    x = episode order/date, y = currency move over the next 8 weeks
    (positive = currency up), green/red + ▲▼ (price moves), plus a chip per
    episode "Mar 2015 · ▲ +3.2%".
  - If not at an extreme: say so ("not at an extreme right now, N of 100, so
    there are no comparable past cases").
  - "Show all bands" toggle → band table (times, median 4W/8W move, % price
    went against them); hide bands with < 5 episodes ("too few").
  - Takeaway: what shows / now ("In X of N past cases the price went against
    the crowd within 8 weeks; in Y, the crowd kept being right.") / why
    ("past episodes, not a forecast … gap between best and worst outcome").
- "Why this may differ from other sites" note: many sites show the Legacy
  report's "non-commercials", which mix hedge funds and asset managers; this
  page separates them (fast vs slow money).
STOP — ≤10-line status.

## PART C — TAKEAWAYS FOR EVERY POSITIONING PANEL
Templates (examples of the intended tone; use live values):
- Header summary — now: "The biggest story this week: hedge funds are heavily
  betting against JPY and have started cutting those bets."
- 1 Big traders — now: "Hedge funds' largest bets are against JPY and NZD.
  On AUD, hedge funds and long-term investors are betting in opposite
  directions." Matters: "Extreme bets leave little room to add, so surprises
  the other way can cause fast moves."
- 2 Flows — now: "This week hedge funds added most to GBP and cut their NZD
  bets the most."
- 3 Squeeze watch — now: "JPY has the highest squeeze risk: hedge funds are
  heavily short and the yen has started rising against them."
- 4 Positioning vs price — now (selected currency): describe the last 4 weeks
  in one sentence (position change vs price direction; divergence if flagged).
- 5 After past extremes — now: "Hedge funds were this short JPY N times since
  2010; in X of those, the yen rose over the next 8 weeks." Matters: "Past
  episodes, not a forecast." Hide when fewer than 5 episodes.
- 6 Institutions vs retail — now: "On N of 28 pairs, retail traders are on the
  opposite side of hedge funds; the clearest case is EUR/USD."
- Glossary entries at least: hedge funds / fast money, asset managers / slow
  money, dealers, COT report, open interest, net position, percentile,
  crowded, squeeze, retail traders, % by volume.

## PART D — PAGE UPDATE
- Positioning page uses the redesigned section 1, the new lookback and units,
  takeaways on every panel, glossary hovers, and the Explanations switch.
- Responsive to 390 px; states; shared nav + freshness indicator.

## PART E — LIGHT THEME + SEMANTIC TOKENS (see DECISIONS_THEME_LIGHT.md)
- Introduce the token set for both themes, the Light/Dark toggle (default
  Light), ECharts theming from tokens, and replace raw colours with tokens
  across templates and chart configs.
- Positioning page must match PositioningLight.dc.html in light and the
  dark mockup in dark. Other pages: smoke-check both themes (no unreadable
  text, charts re-themed); list anything that needs a page-specific fix.
- Colour rule everywhere: up/on/long = --up, down/against/short = --down,
  caution = --caution, neutral data = --ink/--ink-2, interface = --accent.
  No violet or blue data series.

## TESTS
Theme tests from DECISIONS_THEME_LIGHT.md;
Pair dollar values (contract size × price, sign flip for USD-base pairs,
no DX subtraction, cross = base − quote, "implied" label); pair API;
Template outputs for typical / extreme / missing data; banned-words check
(will, likely, expected to, forecast) on all takeaway outputs; sentence count
≤ 2 per part; glossary terms resolve; Explanations switch hides takeaways;
coverage test for all Positioning panels; side/extreme/sort rules; notional
sign; adding/cutting logic; 52-week lookback. Full suite green in-container.

## DELIVER
REPORT_STEP18.md (≤60 lines) + ≤10-line status after Part B and at the end.
## Positioning wording amendment (2026-10-29)
- Say "bet on / bet against"; never "long / short / net position" in displayed wording.
- Identify figures as changes ("added $1.15bn to") or levels ("a $9.66bn bet against").
- Always name the quoted market (for example "the yen against the dollar"); never "the price".
- Apply to every Positioning template and chart label; internal field names stay compatible.
