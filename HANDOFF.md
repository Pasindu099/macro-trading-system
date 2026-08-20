# HANDOFF

Running log of what was built, what was deliberately deferred, and what the next
session needs to know. Architectural conventions live in `CONTEXT.md`; this file
is the session-to-session state.

---

## 2026-08-20 — Event-driven policy delta panel (frontend)

Built the dashboard panel over the scoring layer from 2026-08-19, and **ran the
0016 migration + backfill** that the previous session left pending.

### Migration and backfill are now done

```
alembic upgrade head          # 0015 -> 0016_event_innovation
python -m scripts.build_event_innovation --truncate
```

Result: 17,308 deduplicated releases → 5,563 scored, 11,745 stored unscored,
678 bundles, 173 meeting-adjacent rows awaiting the CB roll-up.

Distribution sanity-checked and healthy: `stddev(surprise_normalized)` = 0.98
(unit scale, as designed), mean |z| = 0.64, only 9 of 5,563 clipped at ±6 —
the fat-tailed-but-not-broken profile the scaling was aiming for. Bundle counts
land at ~70 per key across 2020-2026, i.e. one per month, as expected.

### What was built

**Endpoint** — `GET /panels/event-innovation` in `app/api/routes/pages.py`.
Query params: `days` (default 14, clamped 1-90), `country`, `include_unscored`
(default false). Returns the HTMX fragment.

**Service** — `app/services/event_innovation_feed.py`. Loads scores + bundles,
collapses bundled members into one row each, and **computes decay live at
request time** from `half_life_days` and days-since-release. Nothing decayed is
persisted — how much of a shock survives depends on when you ask. The decay
maths is imported from `app.processing.event_innovation` rather than re-derived,
so the panel and the scoring layer cannot drift.

**Template** — `app/web/templates/_event_innovation.html`. One row per bundle or
standalone release, newest first. Row expansion uses native `<details>` rather
than an HTMX round trip: the member numbers are already in the payload, so a
network hop to reveal them would only add latency and a spinner.

**Styles** — `.eid-*` block appended to `main.css`, following the `.rl-*`
release-ledger conventions and the macro_design tokens.

**Placement** — collapsible `<details class="tpanel tpanel--collapsible">` in
column 3 of the landing terminal, collapsed by default and deliberately *not*
beside the Currency Bias Board. Loads on first expand
(`hx-trigger="toggle once from:closest details"`) and then refreshes every 120s
**only while open** (`every 120s [this.closest('details').open]`), so a collapsed
panel costs nothing.

**Tests** — `tests/unit/test_event_innovation_feed.py`, 33 tests. Full suite:
147 passing.

### Design decisions worth knowing

**Hawkish/dovish uses `--hawk`/`--dove`, not `--bull`/`--bear`.** Both pairs
already exist in `macro_design.css`. This panel answers "which way does this push
the rate path", so it reuses the existing policy-direction palette; the green/red
currency-move palette would imply a P&L reading the number does not carry.

**The decay bar reads in two parts.** The track width is the shock size at t=0
(scaled against `BAR_FULL_SCALE = 3.0`, since scores clip at 6 but a 3-sigma
print is already outsized); the fill inside it is the fraction surviving now.
A single-value bar cannot show size and remaining life at once. Track width is
floored at 2% so an exactly-in-line print reads as "no surprise" rather than as
a broken row.

**Bundle labels are derived, not configured.** `US_NFP_DAY` → "NFP Day": the
country already has its own column, and a `label:` field in bundle_config.yaml
would be a second thing to keep in sync.

### Bugs found and fixed this session

**`:param::cast` silently breaks SQLAlchemy `text()`.** SQLAlchemy's bind-param
regex has a negative lookahead for `:`, so `:country_code::text` is *not* parsed
as a bind param — it is left in the SQL as literal text and Postgres rejects the
statement. Both this module and `event_innovation.py` now use
`CAST(:country_code AS text)`. Worth remembering: it fails at execution, not at
import, and only on the code path that uses the parameter.

**Bundles were leaking members, which the panel made visible.** UK CPI day was
rendering as three rows (the bundle plus two loose MoM prints) — exactly the
"one event, N shocks" problem bundling exists to prevent. A systematic query for
"scored releases with no bundle_id on a day that has a bundle" found 12 such
indicators. Added the genuine same-release members to `bundle_config.yaml`:

- `UK_CPI_DAY`, `CA_CPI_DAY`, `EU_HICP_DAY`, `JP_CPI_DAY` ← the `*_mom` variants
- `UK_LABOUR_DAY` ← `employment_change` (71 escapes, the worst offender)
- `NZ_LABOUR_DAY` ← `labour_cost_index_yoy`
- `AU_CPI_DAY` ← `monthly_cpi_indicator`

Re-running the query afterwards leaves only three, all correctly standalone:
US `initial_jobless_claims` / `continuing_jobless_claims` (a weekly Thursday
release that merely coincides) and EU `unemployment_rate` (a labour print landing
on HICP day, with no EU labour bundle to join). Bundles went 674 → 678.

**Same-day duplicate labels.** The ONS publishes quarterly and monthly GDP (YoY)
on one morning, both mapped to `gdp_yoy`, so the panel showed two identical
"GDP (YoY)" rows. Not a bug — two genuine periods — but unreadable, so the row
subtitle now carries the period.

### Verified

Endpoint exercised across every branch via TestClient: default window (25 rows),
`country=US` (17), narrow window → empty state renders, `days=99999` → clamped to
90 (152 rows), `include_unscored=true` (81). Landing page renders with the panel
wired in. Template rendered end-to-end against live data.

### Stubbed / not built (per brief)

- Projection tracker badges — needs `cb_projections` / `cb_tracking`.
- Drill-down-per-currency views. The endpoint already takes `?country=`, so the
  data path exists; only the UI is missing.
- Real-time push. Polling only.

### Candidates for next session

- A `US_JOBLESS_CLAIMS_DAY` bundle (initial + continuing + 4-week average always
  print together) — the last real bundling gap, deliberately left alone because
  it is weekly and high-churn.
- A standalone full-width page for the panel; the fragment is already reusable.
- The `meeting_adjacent` roll-up (173 rows waiting) still blocks on `cb_tracking`.

---

## 2026-08-19 — Event innovation scoring layer

Built the `event_innovation` scoring layer: the canonical surprise measure for
economic releases. Replaces the ad-hoc read-time z-score.

### What was built

**Migration** — `migrations/versions/2026_08_19_0016_add_event_innovation_scoring.py`
(revision `0016_event_innovation`, follows `0015_unverified_entity_flag`).
Creates the `decay_bucket` Postgres enum plus two tables:

- `release_bundles (id, bundle_key, country, release_date, indicator_ids integer[], bundle_score, decay_bucket, half_life_days, member_count, created_at)`
  — unique on `(bundle_key, release_date)`.
- `event_innovation_scores (id, release_id FK, bundle_id FK nullable, indicator_id, release_date, actual, consensus, surprise_raw, surprise_scale, surprise_normalized, decay_bucket, half_life_days, scored, created_at)`
  — unique on `release_id`.

Both are mirrored as ORM models (`ReleaseBundle`, `EventInnovationScore`) in
`app/db/models.py`.

> **Schema deviation from the brief:** `release_bundles` gained `bundle_score`,
> `decay_bucket`, `half_life_days` and `member_count`. The sketch had nowhere to
> put the collapsed bundle score, and a bundle that can't carry its own score is
> only a grouping. `created_at` added to both for consistency with the rest of
> the schema.

**Config** — `config/bundle_config.yaml`. All manual mapping, nothing inferred:

- 13 bundles: `US_NFP_DAY`, `US_CPI_DAY`, `US_PCE_DAY`, `EU_HICP_DAY`,
  `UK_CPI_DAY`, `UK_LABOUR_DAY`, `CA_LABOUR_DAY`, `CA_CPI_DAY`, `AU_LABOUR_DAY`,
  `AU_CPI_DAY`, `NZ_LABOUR_DAY`, `NZ_CPI_DAY`, `JP_CPI_DAY`. Commented stubs at
  the bottom for the next tranche (ISM, flash PMI, retail sales, Tankan, KOF).
- 73 indicators assigned to decay buckets — covers every importance-1 indicator
  across all 8 countries, well past the "top 15" the brief asked for.
- Scoring parameters (EWMA half-life, winsor percentiles, min observations,
  clip, min bundle members, impact threshold) all live here.

**Module** — `app/processing/event_innovation.py`. Pure, DB-free core
(`winsorize`, `percentile`, `ewma_scale`, `surprise_scale`, `normalize_surprise`,
`decay_factor`, `decayed_innovation`, `bundle_score`, `score_releases`,
`build_bundles`) with the I/O (`load_release_records`, `persist`,
`build_event_innovation`) below it.

**Backfill** — `scripts/build_event_innovation.py`.
Full rebuild: `python -m scripts.build_event_innovation --truncate`.
Also takes `--country`, `--date-from`, `--dry-run`, `--config`.

**Tests** — `tests/unit/test_event_innovation.py`, 73 passing. Covers bundle
aggregation (including the NFP-day collapse and weight renormalization), EWMA
scale, winsorization, the decay curve at t=0 / t=½ / t=2×half-life, point-in-time
scoring, the low-impact `scored=false` path, config validation, and an integrity
check that every name in `bundle_config.yaml` exists in `indicator_mapping.yaml`.

### Design decisions worth knowing

**Scales are point-in-time.** Each release is normalized against only the
surprises that preceded it. Costs an O(n²) pass per indicator (~70 prints each —
irrelevant) and makes the table safe to backtest against, consistent with the
existing as-reported / revision-tracking discipline.

**Winsorization is 5th/95th, not 1st/99th.** The brief suggested 1/99. At our
sample size that is a no-op: a monthly indicator has ~70 prints in the 2020-2026
window, so the 99th percentile interpolates to roughly the outlier itself and
clips nothing — exactly the COVID case the winsorization exists to defend
against. 5/95 clips ~3 observations per tail and actually bites. Configurable;
move back toward 1/99 once indicators carry several hundred prints.
`test_percentile_winsorization_is_weak_on_small_samples` records the reasoning.

**EWMA half-life is in observations, not days.** 24 prints ≈ 2 years for a
monthly series. `ewma_halflife_by_frequency` overrides it (quarterly → 8,
weekly → 104) so quarterly series don't end up with a 6-year effective window.

**Direction reuses `indicators.is_higher_better_for_currency`.** That flag
already existed and is populated from `indicator_mapping.yaml`. No second
direction flag was added.

**Impact filtering.** `indicators.importance` *is* the EODHD impact field
(1 = high, 2 = medium, 3 = low). Rows above `scored_importance_max` (default 2)
are written fully populated with `scored = false`, so raising the threshold later
is a re-flag, not a re-ingest. Same treatment for indicators with too little
history or no decay bucket assigned.

**Bundle decay bucket** comes from the heaviest-weighted scored member — NFP day
decays like payrolls (10d), not like the participation rate (21d).

### Deprecated

`app/services/release_ledger.py` was the prior ad-hoc scorer: a read-time z-score
against a **full-history stdev**, never persisted. It still powers the country
page, so it was not deleted — instead its `_surprise_stdevs` now calls
`winsorize` + `ewma_scale` from the new module, so the displayed scores and the
persisted ones use the same maths. Module docstring carries a `.. deprecated::`
note. **Next step: repoint the country-page ledger at `event_innovation_scores`
and delete the local scoring entirely.** It remains whole-window rather than
point-in-time, and does no bundling or decay.

### Stubbed / TODO for next session

- **`meeting_adjacent` roll-up.** `rolls_up_to_policy_baseline()` is a flag only.
  These rows carry a NULL half-life and never decay. The actual roll-up — fold
  the flagged bundle's score into the bank's baseline at the next meeting and
  retire the row — needs `cb_tracking`, which does not exist yet. The backfill
  reports a `meeting_adjacent_pending_rollup` count so the backlog is visible.
- **PCA / factor-model bundle scoring.** `bundle_score()` is a plain weighted
  average with hand-set weights. TODO in place: fit the first principal component
  of the member surprise matrix, or regress realized FX reaction on members, and
  use the loadings as weights. Not worth doing until scores are validated against
  price data.
- **Bundle expansion.** Commented stubs in `bundle_config.yaml`.
- **Auto-detection of bundle membership by timestamp proximity** — explicitly out
  of scope, config-only for now.

### Explicitly NOT built (per brief)

`cb_projections`, `cb_tracking`, the `policy_impulse` combination score, and any
λ / regime weighting.

### Not yet verified

~~The migration has not been run and the backfill has not been executed.~~
**Done on 2026-08-20** — see the entry above for the numbers. The predicted
distribution (unit scale, fat tails, ~70 bundles per key) held.

### Pre-existing failures (not caused by this work)

- `tests/unit/test_admin_routes.py`, `tests/unit/test_scheduler.py` — collection
  errors from missing deps (`python-multipart`, `feedparser`) in the local venv.
- `tests/unit/test_rate_probability.py::test_terminal_reference_override_for_fed`
  — asserts a hardcoded Fed meeting date that has since rolled forward.
