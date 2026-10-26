# Whiteboard Step 4 checkpoint (2026-10-26)

## Step 3 follow-up

Test connection cleanup changed only `tests/integration/test_whiteboard_api.py` and `tests/integration/test_whiteboard_registry.py`; `conftest.py` was unchanged. Each file changed `from app.db.session import get_sessionmaker` to `from app.db.session import dispose_engine, get_sessionmaker` and added `await dispose_engine()` at the end of its test. This releases asyncpg connections before pytest switches event loops.

| Test | Assertions |
|---|---|
| `test_whiteboard_crud_round_trip_and_cascade` | Board/item/link create, patch, delete; invalid currency and kind return 422; two Main opens return the same board IDs; GET restores item config, coordinates, links, viewport; deleting an item removes its links; deleting its test board directly removes remaining items and links. |
| `test_registry_json_and_unique_library_for_all_currencies` | Every fixed widget returns JSON for all eight currencies; each released indicator appears exactly once; categories, `cb_preferred`, and secondary-category search behave as specified. It does not test board CRUD. |

## Step 4 files

- `app/web/templates/desk/page.html`: one Whiteboard link in Deep dive; existing panels and scripts unchanged.
- `app/whiteboard/router.py`: `/desks/{currency}/whiteboard` validates currency, ensures Main board, renders page.
- `app/web/templates/whiteboard/page.html`: board toolbar, sidebar, viewport/world/SVG structure.
- `app/web/static/css/whiteboard.css`: board-scoped CSS variables and minimal light styling.
- `app/web/static/js/whiteboard.js`: board tabs, library drag/drop, pan/zoom, grid snap, move/resize, debounced saves, notes, thesis, links, charts, Fit.
- `tests/integration/test_whiteboard_api.py`: page route assertion added.

## Verification

- Focused API/page test: 1 passed. Focused desk tests: 14 passed.
- `node --check` and Python compile passed. `git diff --check` found no whitespace errors.
- Browser check through a temporary local router-only preview (no auth or scheduler): USD page returned 200; Rates opened; a 2Y/10Y widget was dragged onto Main, moved, and restored at the saved x/y after reload. The test widget and preview container were removed.
- Full suite run did not finish. Pytest remained active without CPU progress or output for several minutes; it was interrupted. No full-suite result is claimed.
- The requested `docs/specs/whiteboard-reference.html` remains absent in this checkout. Styling uses the approved light-theme variables.
