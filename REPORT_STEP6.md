# Step 6 — USD desk

## Part 0: Step 5 follow-ups

- USD/SEK was added to FX ingestion (`fx_spot.py`; it is a DXY component and not a G10 pair). The backfill 2010-01-01 → 2026-10-03 used 1 history call and 1 listing call, and inserted 4,443 rows.
- `app/services/dxy.py` computes ICE DXY from the six components on common dates. It is stored in `fx_spot_observations` as pair `USD/DXY`, `source_type='computed_dxy'`, and is rebuilt inside `job:rates_derived` after flagging. That gives 4,428 rows, 2010-01-01 → 2026-10-02. Check: 77.46 (2010-01-04) and 113.96 (2022-09-27). The outlier flagger skips the computed series.
- Positioning USD spot is now the computed DXY; the equal-weight index was removed. USD squeeze: LF `none`, AM `trend_confirming` (2W +1.77%). USD extremes (episodes / avg 8W % / against-crowd % / max adverse %): >90 13/−0.51/69.2/2.54; 85–90 17/−0.16/56.2/1.85; 10–15 20/+0.59/60.0/1.75; <10 19/+0.64/68.4/2.47.
- API: `pct_reversed_8w` → `against_crowd_after_8w`. New `max_adverse_move_8w` is the largest daily-close move against the crowd within 8W, averaged per band. The 90% coverage rule is unchanged.
- Tests: DXY formula and component completeness, adverse move, renamed field. The Step 4 pair-count test now expects 28 G10 pairs + USD/SEK. Full suite: 362 passed, 7 skipped, 1 deselected.
