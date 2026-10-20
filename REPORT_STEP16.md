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

## Part C — BoJ Outlook projections

- Automated official HTML highlights (GDP/core CPI medians) with installed `pypdf` fallback for core-core CPI, medians and ranges. Only explicitly labelled forecast cells are loaded; the 2020 tax-adjusted CPI reference column is excluded.
- Added `cb_projection_round_sources` for validated source publication date, HTML URL and optional PDF URL. The source date is also the stored round date, so HTML highlights appear only after their publication; the CB panel shows date and source link.
- Local 2020 onward backfill: 27 validated rounds, 646 values, 15 rounds led by HTML, latest source 17 Aug 2026. April 2020 stores 12 published range bounds and is labelled "ranges only"; no median is stored.
- `/admin/cb-imports` retains manual values as fallback, with the entered date labelled as the source publication / round date.
- Focused parser/manual/terminal tests: 24 passed. Full container suite: 810 passed, 7 skipped, 1 dependency warning.
- April 2020 correction: focused parser tests 15 passed; full container suite 811 passed, 7 skipped, 1 dependency warning.

## Part D — experimental model-implied policy rate

- Added daily point-in-time snapshots with dated inputs, source URLs, version, 3-month implied market rate, sensitivity range and confidence (`app/services/policy_model.py`, `config/policy_model.yaml`). The model is display-only; it does not affect verdicts or pair sorting.
- NY Fed HLW supplies US, euro-area and Canada neutral rates; OECD Economic Outlook supplies output gaps. The NY Fed workbook has no UK neutral-rate series. BoJ's published neutral range is represented by a dated low-confidence midpoint. Missing inputs remain unavailable.
- 20 Oct local snapshot: FED 4.3919%, +18.43 bp, medium; BOJ 1.28%, +5.19 bp, low. ECB lacks fresh 3-month pricing; BOE lacks r*; BOC lacks fresh core inflation and a 3-month tenor; RBA lacks r*; RBNZ lacks r*, core inflation and fresh pricing; SNB lacks r*, core inflation, target and a 3-month tenor.
- API: `/api/cb/{bank}/model-policy`; daily scheduler refreshes snapshots. Focused tests 4 passed; full container suite 815 passed, 7 skipped, 1 dependency warning.
