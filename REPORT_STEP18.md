# Step 18 - Takeaway framework and Positioning (2026-10-29)

| Part | Delivered | Verification |
|---|---|---|
| A | Shared macro/CSS, deterministic registry, glossary, Explanations cookie switch | Focused 40 passed/1 skipped; full 929 passed/8 skipped/1 warning (140.17s). |

- Supplied decisions saved in `DECISIONS_STEP18_TAKEAWAYS_POSITIONING.md` and `DECISIONS_THEME_LIGHT.md`; both supplied mockups copied to `design/design-mockups/`.
- `app/services/takeaways/`: one function for each of seven Positioning panels; three-part contract with explicit unavailable/stale text. Live readings and per-panel placement remain Part C.
- `_takeaway.html`, `takeaways.css`, `config/glossary.yaml`: escaped text, accessible native details/summary glossary, longest-term matching; coverage and writing-rule tests.
- `explanations.js` and base header: On by default, cookie remembered; Off hides marked explanations while retaining numbers/charts. Existing long summary helper marked for hiding.
- Browser: 1440/390, actual Chromium keyboard/touch definitions and switch/cookie/reload passed (`tests/browser/check_takeaways.cjs`). Node-only unit test skipped in the Python container; browser check covers the real JS.
- Part B in progress. Parts C-D and E are deferred to the requested later sessions; no themes or production deployment changed.
