# Step 11 — Calendar (stopped after Part C)

| Part | Result | Commit |
|---|---|---|
| A — data service | Day-bounded arbitrary past/future ranges, bundle collapse with same-country/time fallback, components, revisions, Event Innovation σ, source status and impact overrides. CN calendar ingestion and 11 mappings. | `e3fafcb` |
| B — fast actuals | 60-second guarded job; polls pending High events during [release, +10 min), then scores confirmations. | `7eab218` |
| C — calendar list | Date presets, custom range, currency/impact/surprise filters, timezone cookie, sticky day groups and progressive HTMX day loading. | current commit |

- Service: `app/services/calendar_service.py:1`; overrides: `config/calendar_overrides.yaml:1`.
- CN ingestion: `app/ingestion/eodhd_client.py:47`, `config/countries.yaml:12`, `config/indicator_mapping.yaml:40`. CN is absent from Macro State mandate weights; unmapped CN releases remain visible as unmapped components.
- Fast polling: `app/ingestion/scheduler.py:89` and `app/ingestion/scheduler.py:376`.
- List route: `app/api/routes/calendar.py:1`; templates: `app/web/templates/calendar/page.html:1`, `app/web/templates/calendar/day.html:1`.
- Focused tests: Part A 3 passed; Part B 1 passed; Part C plus shell 18 passed.
- Full suite: Part A stopped at collection because `beautifulsoup4` was missing; added it to dev dependencies. Part B and Part C attempts stalled without a result in the local virtual environment and were stopped. Docker Desktop started, but no project containers were running. Full-suite green is **unverified**.
- CN unmapped event counts could not be measured without the project database container. Mapping names need comparison with live EODHD CN event types.
- Parts D–F were not started, per the requested STOP after Part C.
