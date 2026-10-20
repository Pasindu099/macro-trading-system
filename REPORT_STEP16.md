# Step 16 — GBP, JPY, CAD, CHF desks

## Part A — desk configuration and indicators

- Enabled GBP/BoE/UK, JPY/BoJ/JP, CAD/BoC/CA and CHF/SNB/CH in `config/desks.yaml` with their policy indicators, curve countries and requested key-data charts.
- `check_desk_indicators.py`: GBP 7 mapped / 2 unavailable (services CPI, private-sector pay); JPY 5 / 2 (wages, services PPI); CAD 6 / 0; CHF 3 / 3 (core CPI, KOF, manufacturing PMI). Missing series have `canonical: null` and remain unavailable.
- Existing desk gaps: USD ISM production missing; EUR wage growth and DE/FR core HICP missing.
- Focused desk tests: 10 passed. Full container suite: 803 passed, 7 skipped, 1 dependency warning.
