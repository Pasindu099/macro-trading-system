# DECISIONS — STEP 14: POSITIONING PAGE

Spec: design/design-mockups/Positioning.dc.html. Rules: AGENTS.md; in-container; one part per commit; focused tests; full suite once per part; long jobs detached. No LLM. Descriptive wording only (no forecasts): "in past episodes…", never "expected"/"likely". Session: stop after Part B with a ≤10-line status.

Defaults decided:

COT report: Traders in Financial Futures only (leveraged funds, asset managers, dealers). USD = dollar index contract (existing).
Default lookback: 3 years (switch 1y / 3y / 5y).
Retail positioning: Myfxbook only, via its OFFICIAL API with my own free account. OANDA and IG retail code stays (protected) but is disabled.
Links: currency rows → desk if enabled (USD, EUR), else "desk pending"; pair rows → pair page when it exists, else the Pairs screener.

## PART A — RETAIL SENTIMENT (Myfxbook API)
Credentials from .env: MYFXBOOK_EMAIL, MYFXBOOK_PASSWORD (never logged; add to .env.example as empty keys). If not set: report and continue with retail panels showing "Myfxbook credentials not configured".
Use the official API login → session → community outlook endpoint. Reuse the session until it expires; re-login on expiry. Honest User-Agent, respect rate limits, back off on errors.
Report whether the existing retail service used the API or read web pages, per source (Myfxbook, OANDA, IG, Binance). Disable anything that read web pages; keep the code.
Store snapshots: table retail_sentiment_snapshots(source, pair, taken_at, long_pct, short_pct, long_positions, short_positions, avg_long_price, avg_short_price) — every 4 hours, kept permanently. Daily view = last snapshot of the day.
Pairs: all 28 G10 pairs that Myfxbook covers; report which are missing.
Freshness: source "Myfxbook retail" in config/freshness.yaml (max 8 h).

## PART B — POSITIONING SERVICE ADDITIONS
Summary template: the single most important story (most extreme crowding
squeeze status), e.g. "Speculators crowded short JPY (12th percentile) and shorts are being cut: squeeze watch high."
Release info: data as-of (Tuesday), release date (Friday), next release from a CFTC schedule list in config (holiday shifts included).
Positioning-vs-price divergence flag per currency: over 4 weeks, price direction vs change in leveraged-funds net position disagree AND the position is ≥ 75th or ≤ 25th percentile → "divergence".
Institutions vs retail per pair: COT pair-implied positioning (base percentile − quote percentile) vs Myfxbook % long; flag "opposite sides" when COT-implied is long-biased (> +20) and retail is > 60% short, or the reverse. Otherwise "same side" or "no signal". STOP — ≤10-line status (Myfxbook connected?, pairs covered, scraping found).

## PART C — POSITIONING PAGE (/positioning)
Per the mockup, sections in order:
Summary + release info + "how to use COT" note (weekly, lagged, futures only, extremes can persist).
Crowding map: currency × leveraged funds / asset managers / dealers percentiles with crowded outlines; net contracts; 1W change; read line; lookback switch; clicking a currency selects it for sections 4–5.
Flows: 1W / 4W change bars for leveraged funds, sorted, with a read line.
Squeeze watch: high / medium / trend-confirming cards with evidence and links.
Positioning vs price: 52-week net position bars + price line (DXY for USD), divergence window shaded, read line; currency switcher.
After past extremes: band table for the selected currency (episodes, avg 4W / 8W move, % against the crowd after 8W, max adverse move within 8W), current band highlighted, "past episodes, not a forecast" note; hide stats with fewer than 5 episodes ("too few episodes").
Institutions vs retail: pair rows with COT-implied side, Myfxbook % long, signal chip; a small retail history sparkline per pair once ≥ 7 days of snapshots exist.
States, 60 s cache, shared nav + freshness indicator, responsive to 390 px.
Colours: crowded long/short and flows in amber/blue (positioning, not a price call); green/red only for price moves; ▲▼ next to changes.

## PART D — LINKS ELSEWHERE
Desk positioning panels (USD, EUR) and the Overview ranking link to /positioning with the currency preselected.
Pairs screener (when built) uses the same institutions-vs-retail service.

## TESTS
Myfxbook client (login, session reuse, expiry, missing credentials); snapshot storage + daily view; scraping sources disabled; summary template; release schedule with a holiday shift; divergence rule; opposite-sides rule; extremes table min-episodes rule; page sections (data / pending states). Full suite green in-container.

## DELIVER
REPORT_STEP14.md (≤60 lines) + ≤10-line status after Part B and at the end.
