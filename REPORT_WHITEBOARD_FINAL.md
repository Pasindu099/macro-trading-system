# Currency whiteboard final checkpoint (2026-10-26)

## Browser verification

| Check | Result |
|---|---|
| Labor accordion, search `earn` | Only 3 Average Hourly Earnings and 3 Employment Cost Index rows remained. |
| Five widgets across Central Bank, Rates, Labor, Inflation, Growth; note; thesis; link | Added through the browser. Seven cards and one link persisted. |
| Pan, zoom, reload | Seven item IDs and geometry, note/thesis text, viewport JSON, and SVG link path restored exactly. |
| ECharts | Rate chart initialized; resize changed chart from 334x206 to 434x286. |
| Delete | With toolbar focus, selected note stayed. With board focus, Delete removed only that note. |
| Ask AI and Export | Present and disabled. |
| Direction chips | Bullish and bearish CSS classes use the green and red root variables. Current live widget payloads have no directional statuses; both classes were checked in browser computed styles. |

## Fixes and records

- `app/whiteboard/registry.py`: Labor `earn` search also matches Employment Cost entries. `tests/integration/test_whiteboard_registry.py` covers the match.
- `app/web/static/js/whiteboard.js`: resize ECharts after card geometry changes; scope Delete to the viewport; render payload status chips in card headers.
- `app/web/static/css/whiteboard.css`: bullish and bearish chip colors use `--wb-bull` and `--wb-bear`.
- `context.md` and `HANDOFF.md`: tables, routes, files, CSS variables. `context.md` backlog records the macro rebuild database lock and staging/swap candidate.
- Cleanup: removed seven UI QA boards and two test widgets from Main. Final database counts: 0 items, 0 links, 0 UI QA boards. Main boards remain.

## Checks

- Focused registry test: 1 passed. JavaScript syntax check passed.
- Final full suite: 837 passed, 7 skipped, 1 warning in 101.25s. Local app service restored; preview stopped; temporary scripts removed.
