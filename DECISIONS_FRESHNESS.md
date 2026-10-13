# Data freshness decisions

## Part A — source recovery
- Inspect newest rows, live scheduler registration, and `/api/admin/jobs/status` for FX spot, government yields, FRED, EODHD calendar, COT, and investingLive raw news.
- Repair the cause; catch up sources and then rates derived, incremental event innovation, Macro State, situations, and dominance in that order. Run database work in the Compose app container.
- Record source and derived latest dates in a report of at most ten lines. Commit Part A separately.

## Part B — freshness guard
- Set max ages in `config/freshness.yaml`: FX/yields two business days, FRED daily three days, calendar one day, news six hours, COT ten days, Macro State two days, dominance two business days.
- Show as-of dates and visible stale reason on every panel; stale situation evidence must neither start nor end episodes. Add data freshness to job status. Commit Part B separately.

## Part C — labor situation
- Exit labor deterioration at `≤ 0.3pp` for five days. Between the 0.5pp trigger and 0.3pp exit boundary, display `watch` distinct from active.
- Re-run situations and record changed episodes. Commit Part C separately.

## Verification
- Focused tests for each changed part, then one in-container full suite per part. Include threshold, badge, stale-input, and exact-boundary cases.
