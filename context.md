# ForexCompass — Project Context

_Last rebuilt: 2026-10-03. Last code commit: `77188e8` (2026-08-22)._

**Sources and confidence.** Built from the repo (code, git history of 116 commits, migrations, tests), `HANDOFF.md`, `docs/knowledge_bank.md`, and the previous `context.md` (Apr 2026, now replaced; see it with `git show 77188e8:context.md`). **Past chat history was not available**: only the current session's transcript is on disk. Items that come only from the project brief and are not visible in any file are tagged **[brief-only]**. Tags used below: **[code]** = implemented and in the repo; **[HANDOFF]** = recorded in the session log; **[planned]** = discussed, not built.

---

## 1. Project snapshot

- **Purpose:** G10 FX macro analysis and trading dashboard. It ingests economic releases, scores surprises and central-bank (CB) stance, and shows the results on a terminal-style UI for discretionary FX trading.
- **Target users:** the author (solo developer and trader). It also serves as a public demo and portfolio piece (`f6b5654` made the dashboard a public demo; `scripts/post_context.md` frames it for fintech/data-engineering roles). Auth roles are admin, analyst and viewer.
- **Countries:** US, EU, UK, JP, AU, NZ, CA, CH in dev. **Production has 10**, adding DE and FR, both with `currency_code = EUR`.
- **Stack [code]:** Python 3.11, FastAPI, SQLAlchemy async/asyncpg, Alembic (20 migrations, head `0020`), PostgreSQL 15, Redis 7, APScheduler, Jinja2 + HTMX + vanilla JS, **ECharts**. Legacy Chart.js remains in older pages (`chart.umd.min.js`). Styling is a glassmorphism theme with Roboto and `macro_design.css` tokens.
- **Deployment [code/memory]:** Docker Compose with 5 containers: `postgres`, `redis`, `app` (:8000), `news_pipeline` (:8001) and `telegram_bot`. They run on a Hetzner VPS at `/root/macro-trading-system`, and you deploy with `git pull origin master && docker compose up -d --build` (see the SSH memory file).
- **Data sources:**

| Source | Status | Where |
|---|---|---|
| EODHD economic calendar, 10Y yields, gov curves, FX spot | [code] | `app/ingestion/eodhd_client.py`, `services/government_yields.py`, `services/fx_spot.py` |
| CFTC COT | [code] | `processing/cot.py`, `/cot` |
| FRED | [code], news pipeline only: release-date calendar | `news_pipeline/collectors.py` |
| ECB €STR OIS curve, BoE SONIA OIS | [code] | `services/rate_fetchers/ecb_fetcher.py`, `boe_fetcher.py` |
| CME ZQ Fed funds futures | [code] via **Yahoo Finance** (`ZQ*.CBT`), not CME directly | `rate_fetchers/fed_fetcher.py` |
| Other CB curves (RBA, RBNZ, BoC CORRA, SNB SARON, BoJ TONA proxies) | [code] | `rate_fetchers/*` |
| rateprobability.com | [code] JSON API scraper. Free tier: FED/ECB/BOJ/BOE/BOC/RBA. RBNZ/SNB are Pro-only and shown greyed out | `scraper/rate_probability_scraper.py` |
| Retail sentiment: Myfxbook, OANDA, Binance L/S, IG client sentiment | [code] | `services/retail_sentiment.py` |
| CB RSS, speeches, policy statements and PDFs (8 banks) | [code] | `processing/cb_policy_scraper.py`, `rss_sources.py` |
| News RSS: AP, Al Jazeera, InvestingLive, Fed and ECB press | [code] | `news_pipeline/` |
| Google Drive bank research | [code] | `processing/bank_research.py` |
| Brent Donnelly / Spectra Markets AMFX PDFs (~115 files) | [code] local folder `Brent Research/` | `app/knowledge/` |

- **AI integrations:**
  - **OpenAI [code]** handles CB statement tone and projection analysis, Policy Divergence comparison, news enrichment (news_pipeline), bank-research summaries and Event Log interpretation.
  - **Claude API [code, limited]:** only `scripts/linkedin_post.py` (`claude-sonnet-4-6`). `services/intelligence_context.py` builds a DB-backed currency payload "for Claude-powered macro briefs", but nothing calls it except `scripts/test_intelligence_context.py`. **No Claude brief is wired into the app.**

---

## 2. Built and working [code]

### Currency whiteboard (2026-10-26)

- Migrations `0047_whiteboards` and `0048_whiteboard_currencies` add `whiteboards` (currency, unique currency/name, JSONB viewport, eight currency CHECK), `whiteboard_items` (widget/note/thesis, config and geometry), and `whiteboard_links` (item endpoints). All foreign keys cascade on delete.
- Page: `GET /desks/{currency}/whiteboard`. Boards: `GET /whiteboard/{currency}/boards` and `GET/POST /api/boards`, `GET /api/boards/{id}`. Items: `POST /api/boards/{id}/items`, `PATCH/DELETE /api/boards/{id}/items/{item_id}`. Links: `POST /api/boards/{id}/links`, `DELETE /api/boards/{id}/links/{link_id}`. Viewport: `PATCH /api/boards/{id}/viewport`. Library: `GET /api/whiteboard/library`, `GET /api/whiteboard/library/{category}`. Data: `GET /api/whiteboard/widget-data/{widget_type}`.
- Files: `app/whiteboard/{router,registry,categories}.py`, `app/web/templates/whiteboard/`, `app/web/static/{js/whiteboard.js,css/whiteboard.css}`, `app/web/templates/desk/page.html`, `app/main.py`, `app/settings.py`, `tests/integration/test_whiteboard_{api,registry}.py`. Categories and `cb_preferred` affect this library only; pending sources remain placeholders.
- CSS theme variables on `.wb`: `--wb-bg`, `--wb-card`, `--wb-border`, `--wb-text`, `--wb-muted`, `--wb-accent`, `--wb-link`, `--wb-font`, `--wb-font-size`; direction chips use `--wb-bull` and `--wb-bear`. Other `--wb-*` variables govern spacing, sizing, snap, and zoom.

**Core data layer**
- EODHD ingestion runs 3× daily (Asia, Europe, Americas), with post-release triggers and a YAML canonicalizer (`indicator_mapping.yaml`, about 4.5k lines, alias support).
- Releases are versioned: an `is_latest` flag plus as-reported vs latest views. Backfill from 2020 uses checkpoints.
- Admin: unmapped events, ingestion runs, manual indicator refresh.

**Pages:** `/` (full-bleed monitor terminal with a world map), `/countries`, `/country/{code}` (tabs, release ledger, indicator explorer), `/calendar`, `/rates`, `/fixed-income`, `/cot`, `/news-feed`, `/cb-news`, `/cb-policy-tracker`, `/fx-outlook`, `/macro-monitor`, `/research-lab`, `/rate-prob/{bank}`, `/event-log`, `/knowledge-bank`, `/bank-research`, `/brief-builder`, `/trade-planner`, `/analytics` (CSV/PDF export), plus auth, setup, users and settings.

**Rate probabilities**
- Own engine (`services/rate_probability.py`) over the OIS and futures fetchers, with a meeting calendar and manual "terminal reference" overrides for each CB.
- Separate scraped view from rateprobability.com, scheduled.

**Economic data and scoring layers**
- **Macro State:**
  - `currency_stance` scores inflation, labour and growth per currency.
  - `cb_preferred_score` is mandate-weighted (e.g. Fed 1.5×inflation + 1.0×labour + 0.5×growth) and Z-scored across peers.
  - `cb_reaction_score`, `macro_indices` and currency-strength models feed `/fx-outlook` and the landing page Currency Bias Board.
- **Event Innovation (2026-08-19 to 08-21):**
  - Canonical surprise score: point-in-time EWMA scale, 5/95 winsorization, clip at ±6σ.
  - 13 manual release bundles (NFP day, CPI days, labour days) collapse to one score each. 73 indicators are assigned to decay buckets.
  - Tables `event_innovation_scores` and `release_bundles` (migration `0016`).
  - Backfill: `scripts/build_event_innovation.py`. Last local run: 5,563 scored, 678 bundles, σ≈0.98.
  - **Policy-delta panel:** `/panels/event-innovation` HTMX fragment, collapsible on the landing page. Decay is computed live, with filter chips and DE/FR country labels.
- **Fixed income (2026-08-22):** government-yield and FX-spot ingestion (migrations `0019`/`0020`), curve slopes, rate differentials, a regression "rates-implied" fair value and rate-repricing tests.

**Retail sentiment:** merged cached snapshot (TTL 300s) on `/fx-outlook`.

**Economic calendar:** `/calendar` with country, category and importance filters, plus structured event-prep matrices.

**CB document pipeline**
- Scrapes statements for 8 banks. OpenAI returns tone score, outlook, key phrases, tone shift vs the prior meeting and trader bullets.
- Caps: 12 reports and 2 stored PDFs per bank.
- Manual PDF upload and `data/policy/` ingester. Economic projections are extracted and charted as forecast paths.
- Tone timeline and Policy Divergence tab (raw PDF text sent to the AI). CB speech and report feeds with speaker tags.

**News**
- `news_pipeline` service polls RSS every 45–90s and FRED every 15 min. It does OpenAI enrichment with an unverified-entity guard, takes price snapshots after each headline, sends macro-flash items to a Redis stream and pushes to a **Telegram bot** (topic threads).
- In-app Intelligence Monitor with tier 1/2/3 news-monitor jobs.

**Event Log ("news logger"):** you enter a calendar release by hand, add forecast and actual, get an AI interpretation, and paste in price reactions. Pip and % changes are computed server-side (migration `0010`).

**Research Lab:** multi-currency research checklists with tone controls, charts attached to rows and PDF export with point toggles. It also has correlation views (`correlation_service.py`, `correlation_lab.js`, correlation cache table).

**Knowledge Bank / RAG foundation (Phase A, 2026-08-21/22)**
- Idempotent PDF scan of `Brent Research/`: SHA-256 dedup, page-level text via `pypdf`, sections, PyMuPDF figure and table extraction with filtering.
- Pages `/knowledge-bank` and `/knowledge-bank/documents/{id}`.
- Pydantic schemas for claims, trades and recommendations. Deterministic hybrid-retrieval ranking primitives.
- Migrations `0017`/`0018`.

**Tests:** 258 pass, 1 fails (known), 24 integration tests error. The errors likely come from no local DB; not investigated.

---

## 3. Key design decisions

Origin is the HANDOFF or code rationale unless noted. Whether each was **your call or Claude's recommendation is not recorded** in the files; HANDOFF entries read as Claude's implementation choices made inside a brief you wrote.

- **3-layer scoring: Macro State → CB Tracking → Event Innovation, combined later into `policy_impulse`.** Layers 1 and 3 exist. The CB Tracking layer (`cb_tracking`, `cb_projections`) and `policy_impulse` were **explicitly out of scope** in the 2026-08-19 brief.
- **Bundling is config-only, never inferred from timestamps.** Co-released prints would otherwise count one event N times. A bundle's decay bucket is taken from its heaviest member.
- **Point-in-time scales** keep the scores safe to backtest, consistent with the as-reported revision discipline.
- **Winsorize at 5/95, not the brief's 1/99.** About 70 prints per indicator makes 1/99 a no-op. Revisit once indicators have several hundred prints. (A deviation from your brief, documented in HANDOFF.)
- **EWMA half-life is counted in observations,** with overrides by frequency.
- **Low-impact rows are stored with `scored=false`,** so raising the threshold later is a re-flag rather than a re-ingest.
- **Decay is computed at request time, never persisted.**
- **Hawk/dove colours (`--hawk` amber / `--dove` indigo), not bull/bear,** for policy direction. The reference design's blue/coral is aliased and costs a two-line swap.
- **Knowledge Bank keeps immutable source PDFs plus a DB as the canonical store.** Embeddings and model outputs are regenerable derived artifacts. Extraction is versioned so Docling or PyMuPDF can replace `pypdf`.
- **No hard-coded news provider.** Feeds normalise into `knowledge_news_events`.
- **Rate probabilities:** free market data first (Yahoo ZQ, ECB/BoE APIs, proxies), with manual overrides to match the terminal reference. rateprobability.com runs as a parallel cross-check.
- **Security:** the Postgres port is no longer published (`3a19219`).
- **[brief-only], not found in any file:**
  - **dominance hierarchy:** the only trace is an unused `resulting_dominance_state_id` / `dominant_tier` field in news alerts.
  - **"Fed + ECB first":** not found.
  - **pgvector + bge embeddings:** no pgvector extension, no embedding code, and the `knowledge_embeddings` table is empty by design in Phase A.
  - **n8n for pipelines:** `Documents/n8n/` is empty. The pipelines are APScheduler-based.
  - Confirm these from your own notes.

---

## 4. Partially done / in progress

- **Last commit `77188e8`, 2026-08-22: Knowledge Bank Phase A plus the fixed-income layer.** Work stopped after Phase A. Not yet built:
  - Phase B: structured extraction with an LLM, a single-document reprocess command and review/edit endpoints.
  - Vision interpretation of charts.
  - Embedding generation.
  - Phase D: manual headline-analysis page.
- **Uncommitted local files:**
  - `data/fixed_income_{desktop,mobile}.png` (UI verification screenshots)
  - fx-spot and gov-yield backfill checkpoint JSONs
  - `data/indicator_correlations/` (US ISM Manufacturing PMI correlation and walk-forward CSVs from July)
  - These look like artifacts, but **whether the yield and FX backfills finished is unknown.**
- **Event Innovation follow-ups (HANDOFF):**
  - 173 `meeting_adjacent` rows never decay because they wait on `cb_tracking`.
  - Country-page `release_ledger.py` is deprecated but still live. **Next step: repoint it at `event_innovation_scores` and delete its local scoring.**
- **Stubs:** projection-tracker badges on the panel, per-currency drill-down UI (the endpoint already accepts `?country=`), the `/trade-planner` and `/brief-builder` scaffolding, and the Claude macro-brief payload (built, not wired).

---

## 5. Known bugs and open questions

| # | Item | Status |
|---|---|---|
| 1 | **Prod bundle membership is stale.** Members show as sibling rows. The fix is data only: `docker compose exec app python -m scripts.build_event_innovation --truncate` on the VPS | Open. **Unknown whether it has been run** |
| 2 | **Deploy branch mismatch.** The deploy command pulls `origin/master`, which is at `ddf531f` (Aug 4). The latest 14 commits are on `origin/main` (= local `master`, `77188e8`). Yet HANDOFF describes prod data from Event Innovation | **Unclear what the VPS is running.** Check `git log -1` on the VPS |
| 3 | **Migrations 0017–0020 on prod** | Unknown |
| 4 | **Secrets committed in `docker-compose.yml`:** the EODHD API key and Postgres password are hard-coded and in git history | Open. Rotate and move them to `.env` |
| 5 | **OpenAI model config conflict:** `settings.py` defaults to `gpt-5.5-pro`, but compose sets `OPENAI_MODEL` to `gpt-4o-mini` (app) and `gpt-4o` (news_pipeline), so compose wins unless `.env` overrides it | Open. Decide the model and cost |
| 6 | `test_terminal_reference_override_for_fed` asserts a hard-coded meeting date (2026-06-17 vs 2026-10-28) | Known, failing |
| 7 | 24 integration-test errors in `tests/integration/test_admin_endpoints.py` | Likely missing local DB. Unverified |
| 8 | Missing local deps (`python-multipart`, `feedparser`) broke test collection in HANDOFF | Seems resolved (they collect now) |
| 9 | `:param::cast` silently breaks SQLAlchemy `text()`. Use `CAST(:p AS type)` | Fixed. Keep as a convention |
| 10 | Exact-0.00σ rows render flat grey; hiding them is a one-line filter | Your call, open |
| 11 | Panel colours: app hawk/dove vs the reference's blue/coral | Your call, open |
| 12 | Leftover `tmp_db_probe.py` and `test_settings.py` in the repo root | Cleanup |

---

## 6. Deferred tech debt (explicitly postponed)

- Macro rebuild holds `AccessExclusiveLock` on `currency_stance` and blocks `/desks` reads; candidate fix: build into staging, then swap.

- PCA or regression-based bundle weights instead of hand-set ones. Wait until scores are validated against price reactions.
- More bundles: ISM, flash PMI, retail sales, Tankan, KOF (stubs in `bundle_config.yaml`), plus `US_JOBLESS_CLAIMS_DAY` (left out as weekly and high-churn).
- Auto-detecting bundle membership by timestamp: out of scope.
- λ / regime weighting.
- Real-time push for the panel (it polls only).
- Moving winsorization back toward 1/99 as history grows.
- Knowledge Bank: Docling extraction, review workflow (read-only today).
- Plain-English summary: `is_higher_better_for_currency` is a UI default only; turning it into a scoring rule was labelled "Phase 2" in `config/README.md`.

---

## 7. Roadmap

- **Research Lab / Correlation Lab:** **partially built already** (checklists, correlation views, correlation cache, ISM correlation study). What is left beyond that is not specified in any file; define the scope.
- **CB Tracking layer [planned]:** `cb_tracking` and `cb_projections` tables, the meeting-adjacent roll-up, the `policy_impulse` combination score and projection badges.
- **Phase 2 [brief-only, details not in files]:** pair analysis (base vs quote across layers) and alerts. Existing hooks: Telegram bot, Redis macro-flash stream, news alerts.
- **Phase 3 [brief-only]:** predictive models and backtesting. Groundwork exists: point-in-time scores, as-reported releases, `pmi_forecast_backtest.py`, `modeling_workbench.py`, Event Log price reactions, news price snapshots.
- **RAG agent [planned]:**
  - Phase B: structured extraction (claims, frameworks, trades).
  - Then embeddings and semantic retrieval. The stack is unconfirmed; pgvector + bge is [brief-only].
  - Then Phase D: headline analysis with no-trade guardrails and a recommendation accountability log.
- Standalone full-width policy-delta page and per-currency drill-down.

---

## 8. Restart plan

1. **Re-establish ground truth on the VPS.**
   - Check which commit it runs and whether migrations 0017–0020 are applied.
   - Fix the deploy branch so `main` and `master` are aligned and the deploy command pulls the right one.
   - Deploy `77188e8` and run the `build_event_innovation --truncate` backfill (bug #1).
2. **Security and config hygiene.** Rotate the EODHD key and DB password, move both to `.env`, and settle `OPENAI_MODEL` (bugs #4 and #5).
3. **Get to a green baseline.** Make the Fed-date test date-independent, start the local DB and triage the 24 integration errors. Delete the root temp files and commit or gitignore the untracked `data/` artifacts.
4. **Finish the Event Innovation migration.** Repoint the country-page ledger at `event_innovation_scores` and delete `release_ledger` scoring. This one is small and closes the "two sources of truth" problem.
5. **Build the CB Tracking layer** (`cb_tracking` / `cb_projections` → meeting-adjacent roll-up → `policy_impulse`). It is the missing middle of the 3-layer model and unblocks projection badges and pair analysis.
   - Then pick between Knowledge Bank Phase B (RAG) and Phase 2 alerts.
   - **Decide first whether pgvector + bge is still the plan.**
