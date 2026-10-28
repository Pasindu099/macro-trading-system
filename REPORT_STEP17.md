# Step 17 - Data services (2026-10-28)

| Part | Delivered | Container verification |
|---|---|---|
| A | Indicator catalogue, revision-aware history, persisted surprises, bounded CSV stream | Focused: 9 passed. Full: 859 passed, 7 skipped, 1 warning (172.23s). |

- A: `app/services/data_library.py`; every mapped indicator, including empty histories; 11 country codes; CN flagged calendar-only. Macro State unchanged.
- Revision chains retain every actual print; first/latest values, revision size and actual-change count. Null surprises stay null when no persisted score exists.
- Surprise source: direct `event_innovation_scores` join. Application callers changed: **0**; repository scan confirms no runtime imports of deprecated `release_ledger`. Old module retained.
- `app/services/data_exports.py`: connection-owned streaming CSV, 50,000-row SQL and iterator caps, download headers, spreadsheet formula escaping.
- Tests: `tests/integration/test_data_library.py`; rollback-only database fixtures.
- Parts D/E not started; no page, link or redirect claims at this checkpoint.
