# Step 17 - Data services (2026-10-28)

| Part | Delivered | Container verification |
|---|---|---|
| A | Indicator catalogue, revision-aware history, persisted surprises, bounded CSV stream | Focused: 9 passed. Full: 859 passed, 7 skipped, 1 warning (172.23s). |
| B | Source health and thirty-day quality inventory | Focused: 13 passed. Full: 872 passed, 7 skipped, 1 warning (95.80s). |

- A: `app/services/data_library.py`; every mapped indicator, including empty histories; 11 country codes; CN flagged calendar-only. Macro State unchanged.
- Revision chains retain every actual print; first/latest values, revision size and actual-change count. Null surprises stay null when no persisted score exists.
- Surprise source: direct `event_innovation_scores` join. Application callers changed: **0**; repository scan confirms no runtime imports of deprecated `release_ledger`. Old module retained.
- `app/services/data_exports.py`: connection-owned streaming CSV, 50,000-row SQL and iterator caps, download headers, spreadsheet formula escaping.
- Tests: `tests/integration/test_data_library.py`; rollback-only database fixtures.
- B: `app/services/data_quality.py`; freshness/job/collector/manual-import composition, five explicit statuses, live scheduler next-run dates when supplied, persisted rates schedule fallback, recursively redacted errors.
- Thirty-day outliers, holiday-aware daily FX/yield/FRED gaps, actual revisions by country, top 20 unmapped names, parser coverage on investingLive Data releases, unavailable model snapshots and current situation inputs.
- Situation unavailability uses current deterministic features; historical unavailable situation states were not persisted by the existing job, so no retrospective states are invented.
- Tests: `tests/integration/test_data_quality.py`; live read-only inventory and health queries, rollback-only quality fixtures, manual/disabled/error states and scheduler/error-redaction checks.
- Parts D/E not started; no page, link or redirect claims at this checkpoint.
