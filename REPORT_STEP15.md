# Step 15 — Pair screener

| Part | Commit | Result |
| --- | --- | --- |
| A | `9846b57` | `pairs_service.screener()` assembles the 28 configured pairs in market order, price changes, verdict differences, numeric policy paths, spreads, drivers, COT, Myfxbook retail, active warnings, and source freshness. |
| B | `4801a43` | `/pairs` table with sortable headers, group and search filters, highlights, Overview matrix view, and `/pairs/{BASEQUOTE}` pending page. |
| C | final commit | Calendar event and Overview pair links use the pending pair route. |

- Default sort is `overall_score` in `config/pairs.yaml`; numeric policy columns show `pending` when both banks lack comparable numeric gap models. The available ECB gap is qualitative.
- All columns sort with pending numeric values last. Overall score sorts by magnitude; clicking a header reverses its direction.
- Retail without a Myfxbook snapshot and other missing data display `pending` rather than fabricated values.
- `python -m compileall` on the changed Python modules and Jinja template compilation passed.
- In-container focused tests: 34 passed after updating two old Step 15 route/link expectations. Full suite: 799 passed, 7 skipped, 1 dependency deprecation warning; no remaining failures.
- Live local app smoke: 28 configured pairs in market order in the service; 28 rows rendered; overall-score, pair, and last sort orders matched the service; a pair link opened its pending page.
- Myfxbook long percentages are explicitly labelled “% by volume”; long and short position counts are shown separately.
- The worktree still contains unrelated pre-existing changes and untracked files; they were not staged.
