# Step 14 — Positioning page (19 October 2026, local; not deployed)

## A. Retail sentiment (Myfxbook official API)
| Source | How the old service read it | Now |
|---|---|---|
| Myfxbook | Official API (login → session → community outlook), but browser-spoofed UA; failed login logged URL with email+password | New client `app/services/myfxbook.py`: session reuse, one re-login on "Invalid session", backoff 2/8/30 s on 429/5xx, honest UA, errors carry type/status only |
| OANDA | Unofficial website JSON (`/cfds/labs/data/open_positions/`), browser UA + Referer | Disabled (`RETAIL_WEB_SOURCES_ENABLED=false`), code kept |
| IG | Regex over two article HTML pages | Disabled, code kept |
| Binance | Official public futures-data API (crypto only) | Kept, honest UA |

- `retail_sentiment_snapshots` (permanent) + `retail_sentiment_daily` view (last snapshot per UTC day), migration 0043. Job `myfxbook_retail` every 4 h (hh:05 UTC). Freshness "Myfxbook retail", max 8 h.
- **Credentials: not set** in `.env` at the time of this report → job reports `not_configured`; retail panels read "Myfxbook credentials not configured". Pairs covered / missing: unknown until the first snapshot (the snapshot result lists missing G10 pairs). Compose files now pass `MYFXBOOK_EMAIL` / `MYFXBOOK_PASSWORD`; recreate the app container after adding them.

## B. Service additions (`app/services/positioning.py`, `/api/positioning/summary|divergence|retail`)
- Summary: most important leveraged-funds story (squeeze status, then extremeness). Live: "Speculators crowded long AUD (89th percentile) and longs are being cut: squeeze watch high."
- Release info from `config/cftc_release_schedule.yaml` (CFTC 2026 schedule incl. 5 holiday-delayed Mondays): as of Tue 13 Oct, released Fri 16 Oct, next Fri 23 Oct 15:30 ET.
- Divergence (decision 1): 4W price vs 4W LF net change disagree, |change| ≥ 0.5 × std of 4W changes over the lookback, percentile ≥ 75 / ≤ 25. Live flags: **none** (CHF's +23-contract change no longer qualifies).
- Institutions vs retail: base − quote LF percentile vs Myfxbook % long; "opposite sides" / "same side" / "no signal" (±20, 60 %).

## C. `/positioning` page
- Seven lazy panels in mockup order (`app/api/routes/positioning_page.py`, `app/services/positioning_page.py`, `app/web/templates/positioning/`): summary + release + how-to-use; crowding map (LF/AM/dealers, crowded outlines, net, 1W ▲▼, read line, 1y/3y/5y, currency select); flows 1W/4W with read line; squeeze cards (high / medium / trend-confirming, evidence, desk link or "desk pending"); 52-week net bars + indexed price (DXY for USD) with divergence spans; past extremes (episodes, avg 4W/8W, % against crowd, max adverse; "too few episodes" < 5; current band highlighted; "Past episodes, not a forecast"); institutions vs retail (28 pairs, sparkline once ≥ 7 daily snapshots, links to the Pairs screener).
- States (loading/pending/empty/partial/error), 60 s panel cache, freshness strips (COT, FX spot, Myfxbook retail), shared nav. Amber/blue for positioning and flows; green/red only for price moves.
- Rendered check (static render, Playwright): 1440 px and 390 px, no horizontal page overflow, no JS errors.

## D. Links
- USD/EUR desk section 7 and the Overview ranking's Positioning cell link to `/positioning?ccy=…#pos-price`.
- Pairs screener not built yet; it should call `positioning.get_institutions_vs_retail()` (`/api/positioning/retail`).

## Decisions after Part B
- Non-release Data headlines retagged Flows & market: 53 items. Parser on real releases (30 days): 58/63 = 92 %, 28 matched, 0 EODHD disagreements (`REPORT_STEP13_FOLLOWUPS.md`).
- `DEPLOY_RUNBOOK.md`: new section with Myfxbook keys, first snapshot, S&P 500 EODHD backfill, retag and parser re-run, smoke checks.

## Tests
Myfxbook client (login, reuse, expiry, backoff, missing credentials, no credential leak), snapshot rows + daily view (DB), web sources disabled, summary template, schedule with holiday shift, divergence incl. size rule, opposite-sides rule, extremes min-episodes, page states/sections, links. Full suite: **794 passed, 7 skipped**.

## First live Myfxbook snapshot — 20 Oct 2026 (Berlin)

- One manual snapshot at 2026-10-19 22:25 UTC stored **28/28** G10 pairs; missing: none. The job recorded success, 28 inserted, one API call and zero errors.
- Stored long/short splits matched the public Community Outlook pages: [EUR/USD](https://www.myfxbook.com/community/outlook/EURUSD) 82/18, [USD/JPY](https://www.myfxbook.com/community/outlook/USDJPY) 61/39, [GBP/USD](https://www.myfxbook.com/community/outlook/GBPUSD) 44/56. The pages refresh frequently, so this confirms the sampled values at check time.
- Recent local app logs: 0 matches for either credential in plain or URL-encoded form. All 3 Myfxbook ingestion records: 0 failed, 0 credential matches in stored error text.
- Redaction now covers `email`, `password` and `session` URL parameters; provider error messages are not copied into Myfxbook exceptions (`app/log_redaction.py:14`, `app/services/myfxbook.py:86`). Focused tests: 13 passed. Full suite: **795 passed, 7 skipped**.

## VPS deployment — 20 Oct 2026 (Berlin)

- Deployed `a1b9c3e` (`v2.4-news-positioning`) to the VPS. Backup retained: 58 MB. App healthy; `/health` returns `ok`; Alembic at `0043_retail_sentiment_snapshots`.
- Step 14 post-deploy: S&P 500 refresh 6,739 rows; headline retag 73 Flows & market; cleared 154 unparsed/unmatched audit rows from the last 30 days; parser rerun: 36 confirmed, 0 discrepancies.
- Existing news pipeline remains running. Telegram bot was observed restarting; its condition was not diagnosed in this deployment.
- VPS `.env` lacks both Myfxbook settings, so a production snapshot and retail panels remain pending. Automatic approval review rejected copying the local credentials to this VPS because the destination was not explicitly authorized for secret transfer. The owner must explicitly authorize that transfer or enter the settings on the VPS.
