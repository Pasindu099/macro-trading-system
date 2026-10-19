# DECISIONS — STEP 13: FX NEWSWIRE

Spec: `design/design-mockups/News.dc.html`.
Rules: AGENTS.md; in-container; one part per commit; focused tests; full suite
once per part; long jobs detached. **No LLM in this step.**
Sessions: stop after Part C and after Part F with a ≤10-line status.

Principles:
- One news pipeline. Everything stored permanently (headlines are small).
- Rule-based tagging and templates now; an AI layer for High-impact items later.
- Telegram alerts: NOT in this step.
- No predictions anywhere. Later analog features use descriptive wording only.

## PART A — SOURCES
- Keep investingLive (main + central banks feeds).
- Add Kitco News via its OFFICIAL RSS feed (URL in config `news_sources.yaml`;
  if not yet set, ask me for it — do not scrape Kitco pages).
- Check FXStreet and ING Think: official RSS available? robots.txt allows the
  feed URL? Connect only if both yes. Report the result.
- All fetchers: the honest identifying User-Agent, respect robots.txt,
  polite intervals (investingLive 45 s as today; Kitco/FXStreet/ING ≥ 5 min).
- Per source in config: `licensed_for_redistribution: false` (personal use).
  The UI must be able to hide unlicensed sources later with one switch.

## PART B — ONE PIPELINE
- Consolidate the two news systems (in-app news_monitor → news_alerts and
  news_pipeline → raw_news/enriched_news) into ONE collection + storage path.
  Pick the better base, report why. Pages, desks and Overview read only
  through `app/services/news.py`.
- Do not delete either system's code or tables (protected); disable the
  duplicate collection job only after the consolidated path is verified
  (same items, no gaps over 48 h, side by side). Report parity.
- Permanent retention; Postgres full-text search index on headline + summary.

## PART C — FILTER, DEDUPE, TAGGING (rule-based)
- FX relevance filter: keep items mentioning a G10 currency or country, a
  central bank, an official from `config/cb_officials.yaml`, a data release,
  or gold/silver/oil (commodity tag). Drop crypto-only, single-stock and
  unrelated items. Count kept / dropped / merged per source per day.
- Dedupe: same story across sources within 2 h (normalised title
  similarity) → one item with "+N sources".
- Tags per item: currencies; type (Data · CB speaker · Politics & fiscal ·
  Geopolitics · Flows & market · Commodities · Analysis); speaker (roster
  match, links to the CB Terminal profile); situation / dominance-tier link
  (active situations and their keywords; e.g. Hormuz/Iran → energy shock).
- Impact: High = CB decisions, voting officials on policy, High-importance
  data releases, items linked to an active high-severity situation; Medium =
  other officials, medium data, commodities reports; Low = analysis, opinion.
- Driver tags for gold/commodity items (Kitco AM/PM reports and similar):
  detect named drivers — treasury/real yields, dollar, oil, safe-haven,
  Fed/rate expectations, inflation, central-bank buying, ETF flows — with
  direction where stated ("falling yields", "firmer dollar"). Store per item.
STOP — ≤10-line status (sources connected, kept/dropped counts, parity).

## PART D — HEADLINE → CALENDAR ACTUALS
- Parse investingLive data headlines (e.g. "US September CPI +0.4% vs +0.3%
  expected") → match the scheduled calendar event (country, indicator,
  ±30 min). Fill actual with status "newswire" until EODHD confirms;
  log any newswire vs EODHD discrepancy.
- Trigger Event Innovation scoring on the newswire actual; re-score on
  EODHD confirmation if the value differs.
- Report match rate on the last 30 days of headlines.

## PART E — "WHY IT MATTERS" TEMPLATES
- Data headline: actual vs consensus + the CB's tracking status
  (reuse the Overview/Calendar template).
- CB speaker: name, role, voter status, focus theme (tone only if the tone
  gate passes).
- Situation-linked: the situation's evidence line.
- Gold/commodity items: the driver tags ("Drivers named: yields ↑, dollar ↑").
- No line when no template fits (never vague filler).

## PART F — EPISODE TAGGING + CROSS-ASSET SNAPSHOT
- Link each news item to any active situation episode at publish time
  (`episode_id`, many-to-many).
- Daily cross-asset snapshot table: gold (XAU/USD), silver, Brent, WTI,
  computed DXY, USD/JPY, USD/CHF, EUR/USD, VIX, US 2Y, US 10Y, US real 10Y
  (DFII10), 10Y breakeven (T10YIE), S&P 500. Sources: EODHD / FRED.
  Backfill from 2000 where available (report coverage per series).
- This is data collection only; no analog matching or flags in this step.
STOP — ≤10-line status.

## PART G — NEWS PAGE (/news)
- Per mockup: sources bar (status, kept/dropped/merged today); "Market-moving
  now" (High, last 6 h); filters: search, currencies, type, impact,
  "only items tied to an active situation", DATE RANGE (any past range);
  feed items: time, impact, type, currency chips, situation tag, source,
  headline, why-it-matters, driver tags (commodities), links (speaker
  profile, desk, pair, Event Log); right column: today's releases with
  surprises, today's speakers + blackout notes, most-mentioned currencies.
- "Price reaction: intraday data pending" once in the header, not per item.
- Desk news panels and the Overview "What's moving markets" use the same
  service and tags.
- States, 60 s cache, responsive to 390 px, shared nav + freshness indicator.
- Colours: amber/blue = hawkish/dovish and beat/miss; green/red only for
  price moves; source label always visible.

## TESTS
Source config + robots check; relevance filter cases; dedupe across
sources; tagging (currency, type, speaker, situation, impact); gold driver
tags with direction; headline parser + calendar matching + discrepancy log;
templates incl. "no line" case; episode linking; snapshot backfill coverage;
page filters incl. past date range. Full suite green in-container.

## DELIVER
`REPORT_STEP13.md` (≤60 lines) + ≤10-line status after each STOP and at the end.

## Part A findings (2026-10-19)
- FXStreet: official RSS `https://www.fxstreet.com/rss/news` and `/rss/analysis`
  (200, text/xml); robots.txt allows both for our UA → connect.
  Note: robots.txt returns 403 to Python's default UA — the robots check
  must fetch robots.txt with our own UA.
- ING Think: official RSS `https://think.ing.com/rss/` (200, text/xml);
  robots.txt allows it → connect.
- Kitco: official RSS URL not set — asked the user.
