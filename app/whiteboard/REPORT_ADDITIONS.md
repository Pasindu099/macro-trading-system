# Whiteboard additions checkpoint - 2026-10-28

| Change | Location |
|---|---|
| Atomic streamed image/card upload; 5 MB limit; PNG/JPEG/WebP magic and MIME validation | router.py:196 |
| Board-scoped image retrieval; item deletion removes image row; image ID protected on patch | router.py |
| Migration 0049: image kind, image table, cascading board FK; downgrade removes image cards | migrations/versions/2026_10_28_0049_whiteboard_images.py |
| Clipboard placement in world coordinates, viewport-centre fallback, editable metadata, shared move/resize/link/delete | app/web/static/js/whiteboard.js |
| Indicator Line/Bar uses existing chart and cached payload; range changes fetch server-filtered history | app/web/static/js/whiteboard.js |
| Calendar range cutoffs; default 5Y; ALL uses all history; invalid ranges return 422 | registry.py; router.py |
| Merge pending item patches to preserve quick metadata, geometry and setting edits | app/web/static/js/whiteboard.js |
| Styling uses existing --wb-* variables; no new libraries | app/web/static/css/whiteboard.css |

| Verification | Result |
|---|---|
| Focused tests | 15 passed in 6.99s; additions + existing whiteboard API tests, in-container |
| Upload tests | Unsupported type, false magic, MIME mismatch, oversize, accepted PNG/JPEG/WebP; mocked persistence |
| Range tests | All five cutoffs, shorter available history, default 5Y, invalid range 422; mocked history/session |
| JavaScript syntax; scoped whitespace check | Passed |
| Full suite | 906 passed, 7 skipped, 1 warning in 117.49s, in-container |
| Live migration up/down/up | Passed locally: 0048 -> 0049 -> 0048 -> 0049 |
| Image/config/geometry and Bar/10Y persistence | Passed through real API/PostgreSQL round trips; retrieval and image-row deletion verified |
| Authenticated release smoke | Passed: 15 Data page/export checks, redirects, image cleanup and Bar/10Y persistence; scripts/smoke_data_whiteboard.py |
| Native browser clipboard interaction | Not reverified in this deployment pass |

- Release includes screenshot cards and indicator settings; temporary QA boards are cleaned up.
- No dependencies installed, Docker restarted, or unrelated user changes modified.
