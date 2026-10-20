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
- Focused tests were added in `tests/unit/test_pairs_service.py`, `test_pairs_page.py`, and `test_pair_links.py` but could not run: the Docker engine pipe is unavailable, and host Python has no pytest. The required in-container full suite is therefore unverified.
- The worktree still contains unrelated pre-existing changes and untracked files; they were not staged.
