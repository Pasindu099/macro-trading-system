# `pages.py` inventory for Step 3.5 — review gate

Source: `app/api/routes/pages.py` at commit `ca67605` plus the Steps 1–3 completion report. This is an inventory only. **No code has been moved or removed.** The file has 105 top-level functions (33 decorated routes, 72 helpers) and 12 nested helpers. Classifications: **A** = data or behavior to preserve in `app/services/` for the new Overview/Desks/Pairs/Calendar/News/Positioning/Central Banks/Data design; **B** = old presentation or transport glue to replace; **C** = unused, remove. For mixed functions marked A, preserve the data calculation and drop old template keys, CSS classes, and URL assembly during extraction. Unit tests must pin data outputs before any move.

## Top-level helpers

| Lines | Helper(s) | Class | Current role / extraction decision |
|---:|---|:---:|---|
| 214–316 | `_flag_for_country`, `_format_value`, `_trend_symbol`, `_meter_percent`, `_meter_color_class`, `_format_score`, `_score_color_class`, `_spread_color_class`, `_format_bp`, `_format_yield`, `_format_map_metric`, `_format_int` | B | Emoji, labels, symbols, CSS classes, display precision and percentages for old cards. New tokens and view models replace these. |
| 298, 306, 320 | `_subtract_months`, `_category_meter_score`, `_percent` | A | Date lookback, legacy category score normalization, and data share math. Preserve behavior where used by service outputs. |
| 326 | `_report_datetime` | B | Date formatting for the old analytics export. |
| 334 | `_build_analytics_csv` | A | Data export for the new Data section; separate tabular content from old headings. |
| 431 | `_build_analytics_pdf` | B | Old ReportLab page design; only carry the underlying analytics data if a new export is requested. Nested drawing helpers listed below. |
| 663 | `_build_analytics_snapshot` | A | Data coverage, categories, countries, frequencies, importance, recent runs, and maintenance indicator options. Move queries and raw metrics to a service. |
| 868 | `_build_currency_stance_dashboard` | C | No references anywhere in `app/` or tests. The old latest CB-score/legacy-stance query is dead; do not carry its presentation shape. The new service should independently handle empty primary scores. |
| 1034 | `_build_fundamental_currency_meter` | A | Latest complete eight-currency CB score, time lookbacks, metric scores; legacy stance fallback already triggers on empty primary output. Preserve data selection and fallback in a service, drop old bar and class fields. |
| 1232 | `_build_world_map_snapshots` | A | Country macro metric preference and country detail data for Overview; drop GeoJSON names, tooltip strings, flag and page URLs if the new overview does not need a choropleth. |
| 1335 | `_json_number` | A | Decimal-to-JSON numeric conversion for data responses. |
| 1345 | `_build_landing_payload` | B | Shape and field casing tailored to `landing_terminal.js`; new API view models replace it. |
| 1463 | `_build_landing_kpis` | A | Strongest/weakest currency, widest yield spread, largest surprise and headline count; preserve calculations, replace card labels/tone classes. |
| 1542–1618 | `_latest_metric_score`, `_metric_change`, `_bias_label`, `_bias_class`, `_change_label`, `_driver_tone`, `_confidence_label`, `_build_actionable_dashboard_insights` | A | Dashboard bias, driver, change and confidence rules. Preserve thresholds and inputs with tests; discard `href`, CSS class and prose built for old cards. |
| 1773–1818 | `_yield_history_point`, `_parse_yield_history`, `_build_yield_chart_data` | A | Yield time-series extraction and chart series; expose neutral numeric series for ECharts. |
| 1879–1926 | `_gbond_symbol_code`, `_fetch_gbond_symbol_set`, `_yield_symbol_prefix`, `_build_maturity_benchmarks`, `_build_fx_chart_data` | A | Symbol discovery, maturity and FX history transformation for Desks/Pairs. |
| 1954 | `_build_maturity_panels` | B | Old rates page panel assembly. Keep its underlying maturity data through the rates service. |
| 1976, 2084 | `_build_rates_research_context`, `_build_yield_differentials` | A | EODHD FX/yield fetches, 1M–10Y curves, 10Y spreads, pair spreads and research data. Strip old display strings and page-only errors on extraction. |
| 2280–2395 | `_repricing_curve`, `_repricing_anchors`, `_repricing_beta`, `_repricing_regime`, `_build_rate_repricing` | A | Shared-date spread change, FX response regression and regime classification for Pairs/Desks. Preserve thresholds and calculations with tests. |
| 2528 | `_build_country_rows` | A | Indicator selection and revision-aware release history by country/category; preserve raw trends and values, drop detail URLs and formatting. |
| 2634, 2659, 2685 | `_row_trend_state`, `_pick_profile_indicator`, `_build_country_profile_cards` | A | Trend and representative policy/inflation/labor/growth indicator choice for country Desks. Drop old card text/classes. |
| 2645 | `_profile_card_from_row` | B | Old profile-card wording and CSS state. |
| 2759, 2813 | `_render_country_template`, `_category_tabs_for_country` | B | Template/HTMX orchestration and old tab labels. |
| 2825 | `_now` | B | Local route timestamp wrapper; services can use a clock directly or inject one for tests. |
| 2831 | `_normalize_news_item` | A | News item normalization; move behind a News service. |
| 2882, 2893, 2922 | `_read_bank_research_admin_state`, `_write_bank_research_admin_state`, `_refresh_bank_research_from_admin` | A | JSON state and background Drive refresh are backend admin behavior. Move to a service and expose through an admin API before deleting old form routes. |
| 2900 | `_bank_research_admin_context` | B | Old admin form context/messages. |
| 3216 | `_build_knowledge_bank_context` | A | Corpus counts, search/filter, document inventory and derived-object counts for Data/research. Move queries to a service. |
| 3648 | `_build_macro_monitor_data` | A | CB watchlist indicator lookup, 18-month release histories, deduped current/previous values. Move to Central Banks/Desks service. |
| 4375–4428 | `_tone_class`, `_tone_percent`, `_outlook_arrow`, `_outlook_class`, `_format_tone_label`, `_format_outlook_label` | B | Old policy-tracker visual tokens and display labels. |
| 4434, 4593 | `_build_cb_policy_context`, `_build_projections_context` | A | Analyzed CB documents, tone histories, forecast paths and forecast-versus-actual comparisons. Move data logic to Central Banks service; remove old CSS classes and display strings. |

### Nested helpers

| Lines | Helper(s) | Class | Note |
|---:|---|:---:|---|
| 454–516 | `start_page`, `round_rect`, `small_label`, `draw_wrapped`, `draw_bar`, `draw_kpi`, `draw_donut`, `draw_bar_list` | B | Eight drawing helpers nested in `_build_analytics_pdf`; old PDF layout only. |
| 1489 | `_surprise_value` | A | Absolute-surprise selection inside `_build_landing_kpis`. |
| 1957 | `available_for` | B | Maturity-panel availability wording. |
| 4610, 4658 | `_is_annual_label`, `_get_actual` | A | Annual forecast filtering and actual indicator/release lookup inside `_build_projections_context`. |

## Decorated routes (all 33 wrappers are B)

The route wrapper and template response are presentation/transport code. Any A data behavior named below must move to services before the route is removed. A route listed as “template only” has no local query; existing `/api/*` endpoints and backend modules remain untouched at this review gate.

| Lines | Route function / path | Data that must remain available |
|---:|---|---|
| 2966 | `landing_page` `/` | Country summaries, surprises, Macro State meter, map metrics, yield spreads, repricing, normalized news, KPIs/insights. |
| 3026 | `rates_page` `/rates` | `_build_rates_research_context`. |
| 3041, 3054, 3067 | `fixed_income_page` `/fixed-income`; `cot_page` `/cot`; `news_feed_page` `/news-feed` | Template only; their data comes from existing services/APIs or page JS. |
| 3080 | `cb_news_page` `/cb-news` | Meter, surprises, yield differentials. |
| 3102 | `trade_planner_page` `/trade-planner` | Pair list derived from yield differentials; old planner UI is presentation. |
| 3122 | `fx_outlook_page` `/fx-outlook` | Template only; retain separate retail sentiment/CB backend endpoints if used. |
| 3135 | `countries_page` `/countries` | Country summaries and indicator totals. |
| 3180 | `analytics_page` `/analytics` | `_build_analytics_snapshot`. |
| 3198 | `bank_research_page` `/bank-research` | Existing cache index and admin state; preserve backend operations. |
| 3408 | `knowledge_bank_page` `/knowledge-bank` | `_build_knowledge_bank_context`. |
| 3428 | `knowledge_bank_document_page` `/knowledge-bank/documents/{document_id}` | Seven document-detail queries listed below. |
| 3516 | `knowledge_bank_figure_image` `/knowledge-bank/figures/{figure_id}/image` | Figure lookup and artifact-path containment check. This binary asset route needs a new API replacement if the new design shows extracted figures. |
| 3727 | `macro_monitor_page` `/macro-monitor` | `_build_macro_monitor_data`. |
| 3740, 3761, 3775 | `refresh_bank_research` `/bank-research/refresh`; `bank_research_admin_page` `/bank-research/admin`; `update_bank_research_admin_page` POST `/bank-research/admin` | State read/write, Drive URL validation, refresh task; migrate to admin service/API before deleting form routes. |
| 3830, 3851 | `event_log_list_page` `/event-log`; `event_log_detail_page` `/event-log/{note_id}` | Event notes come from `event_reaction_log` service; list route also queries country options. |
| 3873, 3886 | `brief_builder_page` `/brief-builder`; `research_lab_page` `/research-lab` | Template only. |
| 3899 | `rate_probability_page` `/rate-prob/{bank}` | Existing rate probability/meeting services plus inline `ois_cache` query and combined market-data view model; move inline query to a service. |
| 4157 | `settings_page` `/settings` | Template only. |
| 4170, 4185 | `analytics_report_csv` `/analytics/report.csv`; `analytics_report_pdf` `/analytics/report.pdf` | Analytics snapshot; CSV export is a Data service candidate. Old PDF layout is B. |
| 4204, 4214 | `country_page` `/country/{code}`; `country_tab_fragment` `/country/{code}/tab/{category}` | Country/category indicator histories. HTMX fragment is B. |
| 4243 | `event_innovation_fragment` `/panels/event-innovation` | Already uses `event_innovation_feed` service; remove HTMX wrapper only. |
| 4276 | `country_releases_fragment` `/country/{code}/releases` | Already uses `release_ledger` service; retain only if new design needs ledger output. |
| 4312 | `indicator_detail_page` `/country/{code}/indicator/{canonical_name}` | Existing public country/indicator lookup. |
| 4342 | `calendar_page` `/calendar` | Country summaries; calendar data is served by existing JSON API. |
| 4748 | `cb_policy_tracker_page` `/cb-policy-tracker` | CB policy and projection contexts, local policy PDF count and total document count. |

## Direct query and external data inventory

This lists every query site or repeated query family in `pages.py`. “Move” means the query and selection rules belong in `app/services/`; the route should only validate inputs and return an API response. Existing service calls are listed separately so their backing behavior is not accidentally removed.

| Lines | Query / operation | Class and destination |
|---:|---|---|
| 665–687 | Release total/mapped/actual/estimate/min/max; counts of countries, indicators, upcoming releases and ingestion runs | A — Data analytics service. |
| 692–812 | Category and country aggregation; frequency and importance distributions; five recent `IngestionRun` rows; country/currency indicator option list | A — Data analytics service. |
| 873–938 | Latest `processed.cb_preferred_score` joined to rankings; exception-only fallback to `processed.currency_stance` | C as currently unused helper; new Macro State service needs its own empty-primary fallback. |
| 1040–1143 | Latest complete eight-currency CB score history; legacy stance query on exception **or empty primary result** | A — Macro State data service. |
| 1232–1310 | `get_country_detail_payload` once per mapped country, then policy/inflation/labor/GDP indicator preference selection | A — Overview data service; public-route function is an existing dependency. |
| 1887–1899 | EODHD GBOND symbol-set fetch | A — rates data service. |
| 1976–2083 | EODHD maturity yield histories and FX histories, plus `_build_yield_differentials` | A — Desks/Pairs rates service. |
| 2084–2279 | EODHD benchmark yield histories, base-currency and pair spread calculations | A — Desks/Pairs rates service. |
| 2395–2525 | EODHD front/long yield and FX histories for repricing regression | A — Pairs service. |
| 2534–2564 | Country/category `Indicator` query, then per-indicator `IndicatorRelease` history query | A — country Desks service; avoid the current N+1 query pattern during extraction. |
| 2882–2962 | Bank research admin JSON state read/write and Drive cache refresh | A — admin service; these are filesystem/external writes, not SQL. |
| 2966–3023 | Calls to public country/surprise services, meter/map/rates builders and InvestingLive fetch | A — Overview/News orchestration, currently inside a page route. |
| 3080–3178 | CB news and country directory calls to existing public functions/builders | A — Central Banks/Desks orchestration, currently inside page routes. |
| 3198–3213 | `load_bank_research_index` and admin-state read | A — research/admin service. |
| 3221–3280 | Knowledge document/file/duplicate/status counts; analyst, publication-year and object-type distributions | A — Data/research service. |
| 3282–3381 | Knowledge document search/status query; per-document page, section, object, trade, framework and source-file counts | A — Data/research service. |
| 3435–3490 | Knowledge document; associated source files, pages, sections, objects, figures and tables (seven queries) | A — document detail service. |
| 3522–3541 | Knowledge figure lookup and artifact-path containment/format checks | A — asset service/API. |
| 3659–3684 | For each of eight banks: indicator-ID lookup and 18-month actual release query | A — Central Banks macro-monitor service; currently two queries per bank. |
| 3835 | Country list for event-log filter | A — existing country service. |
| 3851–3869 | `event_reaction_log.get_event_detail` | A — already a service. |
| 3899–4153 | Rate probability/meeting service calls and direct `ois_cache` source/latest-curve query at 3992 | A — move direct query and mixed view-model calculation into rate probability service; old formatted strings are B. |
| 4204–4342 | Existing public country/indicator/calendar calls, `event_innovation_feed`, `release_ledger` | A — existing services/APIs; old HTMX/template wrappers are B. |
| 4440–4450 | Analyzed statement/report/upload `CbPolicyDocument` query for last 13 months | A — Central Banks policy service. |
| 4600–4685 | All `CbEconomicProjection` rows; per metric matching `Indicator` and latest actual `IndicatorRelease` queries | A — Central Banks projections service; replace the repeated lookups with batched queries. |
| 4761 | Total `CbPolicyDocument` count; also `count_local_pdfs` filesystem read | A — Central Banks status service. |

## Review points before extraction

1. `_build_currency_stance_dashboard` is demonstrably unused. Its empty-primary fallback was never implemented; the new Macro State service should handle this independently, using the already-tested fallback behavior in `_build_fundamental_currency_meter` as a reference.
2. `pages.py` currently mixes data rules with CSS classes, formatted strings, `href`s, and template dictionaries. Pin the numeric/selection outputs before extraction; the new design does not need old visual shapes.
3. The old bank-research form routes and figure-image route perform backend work despite living in `pages.py`. Provide service/API replacements before removing them. Preserve auth, users, roles, existing job/API consumers, all DB tables/migrations, ingestion and processing.
4. The existing `public.py` route functions are imported directly for country and surprise data. During extraction, use service functions rather than route-to-route calls. No change is made at this inventory gate.

**STOP:** Await review of this classification before moving code, tagging `pre-redesign`, removing UI assets, or building the replacement shell.
