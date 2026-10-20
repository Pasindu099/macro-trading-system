# Step 16 — GBP, JPY, CAD, CHF desks

## Part A — desk configuration and indicators

- Enabled GBP/BoE/UK, JPY/BoJ/JP, CAD/BoC/CA and CHF/SNB/CH in `config/desks.yaml` with their policy indicators, curve countries and requested key-data charts.
- `check_desk_indicators.py`: GBP 7 mapped / 2 unavailable (services CPI, private-sector pay); JPY 5 / 2 (wages, services PPI); CAD 6 / 0; CHF 3 / 3 (core CPI, KOF, manufacturing PMI). Missing series have `canonical: null` and remain unavailable.
- Existing desk gaps: USD ISM production missing; EUR wage growth and DE/FR core HICP missing.
- Focused desk tests: 10 passed. Full container suite: 803 passed, 7 skipped, 1 dependency warning.

## Part B — published committee votes

- Added `cb_votes` with member, direction, preferred rate change, official URL and `published_at`; deterministic BoE, BoJ and Fed parsers in `app/services/cb_votes.py`.
- BoE/BoJ committee balance now leads with the latest named policy split and previous split; word-list tone remains below it and gated. UI states that a split measures today's disagreement, not the next decision.
- Local backfill from 2020: BoE 33 meetings / 287 votes, BoJ 28 / 252, Fed 44 / 495. Skipped: BoE 16, BoJ 26, Fed 3 where the official source or named-vote format could not be validated. BoJ PDF-only 2026 releases are among the gaps; PDF extraction damaged names, so no vote was inferred.
- `published_at`: BoE page publication date; BoJ/Fed same-day official statements. Latest stored dates: BoE 17 Sep 2026, BoJ 23 Jan 2026, Fed 29 Apr 2026; old signals show their meeting date.
- Focused vote/terminal tests: 9 passed. Full container suite: 807 passed, 7 skipped, 1 dependency warning.
