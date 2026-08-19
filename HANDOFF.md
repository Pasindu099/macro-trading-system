# HANDOFF

Running log of what was built, what was deliberately deferred, and what the next
session needs to know. Architectural conventions live in `CONTEXT.md`; this file
is the session-to-session state.

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

**The migration has not been run and the backfill has not been executed** — the
Postgres container was not running in this session, so `alembic current` could
not reach the database. Verified instead: both tables compile cleanly against the
Postgres dialect, the revision chains correctly off `0015`, and every module
imports. Next session should run:

```
docker compose up -d db
python -m alembic upgrade head
python -m scripts.build_event_innovation --truncate
```

and sanity-check the resulting distribution of `surprise_normalized` (expect
roughly unit scale, fat tails, clipped at ±6) and the `US_NFP_DAY` bundle count
(expect ~70 monthly bundles across 2020-2026).

### Pre-existing failures (not caused by this work)

- `tests/unit/test_admin_routes.py`, `tests/unit/test_scheduler.py` — collection
  errors from missing deps (`python-multipart`, `feedparser`) in the local venv.
- `tests/unit/test_rate_probability.py::test_terminal_reference_override_for_fed`
  — asserts a hardcoded Fed meeting date that has since rolled forward.
