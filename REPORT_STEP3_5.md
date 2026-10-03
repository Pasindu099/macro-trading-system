# Step 3.5 — old UI retirement and replacement shell

## Recovery point and scope

`pre-redesign` points to `cc8e4fc`, the last commit before the old UI was removed. No database table, migration, ingestion, processing, or job module was deleted. The Step 3.5 work adds no migration; the Steps 1–3 migration remains `0021_job_watermarks`.

The replacement shell has Overview, Desks, Pairs, Calendar (with Event Log), News, Positioning, Central Banks, and Data placeholder pages. It uses Syne, DM Mono, a dark token palette, and ECharts as the only chart library. Login, setup, and user management render with the new tokens. The old rate probability HTML route was removed while its JSON and scrape routes remain.

## Services and public functions

| Service | Public functions |
|---|---|
| `app/services/macro_state.py` | `get_macro_state_board` (legacy stance fallback on primary exception **or empty rows**) |
| `app/services/rates.py` | `get_rates_research_context`, `get_yield_differentials`, `get_rate_repricing`, `get_gbond_symbol_set`, `build_yield_chart_data`, `build_maturity_benchmarks`, `build_fx_chart_data` |
| `app/services/country_data.py` | `list_country_summaries`, `list_biggest_surprises`, `get_country_detail_payload`, `get_country`, `get_latest_indicator_release` |
| `app/services/country_dashboard.py` | `get_country_rows`, `get_country_profile` |
| `app/services/news.py` | `parse_rss_articles`, `fetch_investinglive_articles`, `normalize_news_item` |
| `app/services/bank_research_admin.py` | `read_state`, `write_state`, `get_state`, `save_folder_url`, `queue_refresh`, `refresh` |
| `app/services/knowledge_figures.py` | `get_figure_artifact` |
| `app/services/knowledge_data.py` | `get_knowledge_document_detail` |
| `app/services/rate_probability.py` | `get_market_data_status`, `get_rate_probability_view` |
| `app/services/scraped_rate_probability.py` | `get_scraped_rate_probability_data` |
| `app/services/central_banks.py` | `get_macro_monitor_data`, `get_cb_policy_data`, `get_projections_data` |
| `app/services/legacy_unsorted.py` | `get_analytics_data`, `export_analytics_csv`, `get_currency_meter_data`, `get_world_map_data`, `get_overview_kpis`, `get_actionable_insights`, `get_knowledge_data` |

The rates extraction keeps its live EODHD source and existing calculations. Its TODO names Step 4 and the `government_yield_observations` / `fx_spot_observations` tables. The country service, macro monitor, and projection actual lookup were batched in separate commits after their extraction tests. `public.py` and the replacement data services use the shared country functions; no page route calls another route.

`_build_currency_stance_dashboard` was deleted. `_category_meter_score` remains in `legacy_unsorted.py` because the extracted currency meter uses it. The new Macro State service handles empty primary rows as well as query exceptions.

### `legacy_unsorted.py` contents

This transitional module holds the private analytics snapshot, currency meter with its lookbacks, world map metric preference selection, knowledge search/count queries, the category score formula, and their small calculation/formatting helpers. Its public functions strip page fields such as CSS classes, links, labels, emoji, and formatted numeric strings; the overview KPI and insight public functions return numeric scores, dates, and identifiers. The rates service is intentionally an as-is exception to display-field cleanup under the approved rates decision.

## New backend endpoints

| Method and path | Behavior |
|---|---|
| `GET /api/admin/bank-research` | Read folder/configuration and refresh state; admin role |
| `PUT /api/admin/bank-research` | Validate and save Drive folder URL; admin role |
| `POST /api/admin/bank-research/refresh` | Validate and queue refresh; admin role |
| `GET /api/knowledge/figures/{figure_id}/image` | Serve a figure only from the artifact root, with the original format mapping |

The event log already had every requested admin operation, so no endpoint was added. Its full endpoint list is:

- `GET /api/admin/event-log/candidates`
- `POST /api/admin/event-log/notes`
- `GET /api/admin/event-log/notes/{note_id}`
- `PATCH /api/admin/event-log/notes/{note_id}`
- `POST /api/admin/event-log/notes/{note_id}/ai`
- `PUT /api/admin/event-log/notes/{note_id}/price`
- `GET /api/admin/event-log`

## Deleted files

- Route: `app/api/routes/pages.py`.
- CSS: `app/web/static/css/{cb_policy,cot,fixed_income,knowledge_bank,macro_design,main,rate_probability,research_lab}.css`.
- Geo asset: `app/web/static/geo/world.geo.json`.
- JavaScript: `app/web/static/js/{calendar,cb_news,cb_policy,cb_terminal,chart.umd.min,chart_export,charts,correlation_lab,cot,country_charts,currency_research_lab,event_log,fixed_income,fx_outlook,indicator,landing_terminal,news_feed,rate_probability,trade_planner}.js` and `app/web/static/js/macro_dashboard.jsx`.
- Templates: `app/web/templates/{_country_releases,_country_tab,_event_innovation,analytics,bank_research,bank_research_admin,brief_builder,calendar,cb_news,cb_policy_tracker,cot,countries,country,event_log_detail,event_log_list,fixed_income,fx_outlook,indicator,knowledge_bank,knowledge_bank_detail,landing,macro_monitor,news_feed,rate_probability,rate_probability_scraped,rates,research_lab,settings,trade_planner}.html`.

`base.html` was replaced, and `login.html`, `setup.html`, and `users.html` were retained for auth behavior and restyled. `section.html` and `shell.css` are new.

## Verification

- `docker compose exec app pytest tests/unit -q`: **285 passed, 7 skipped, 1 known failure**. The known out-of-scope failure is `test_terminal_reference_override_for_fed`, which hard-codes June 17, 2026 while the next future override is October 28, 2026.
- `docker compose exec app pytest tests/unit -q --deselect tests/unit/test_rate_probability.py::test_terminal_reference_override_for_fed`: **285 passed, 7 skipped, 1 deselected**.
- `docker compose exec app pytest tests/integration -k jobs -q`: **2 passed**.
- `docker compose exec app pytest tests/integration -q`: **22 passed**.
- `python -m ruff check app/services app/api/routes --select F821` inside the app container: **passed**.
- New tests cover the empty-primary Macro State fallback, figure path containment, bank research folder validation, country selection, batched monitor and projection values, scraped rate probability payloads, and shell/login/setup rendering.

The existing unrelated working-tree files (`context.md`, `PROJECT_AUDIT.md`, and untracked `data/` artifacts) were left untouched.
