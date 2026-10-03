# Step 4 Part A — rates coverage and policy-rate source

Read-only snapshot of the local compose PostgreSQL database on 2026-10-03. Counts include immutable provider revisions; all 39,097 yield rows and 12,589 FX rows have `quality_status = valid`. A gap is the count of missing Monday–Friday dates between consecutive observed dates; market holidays are not removed.

## Government yields

The table has 50 country/tenor series across eight countries. `EZ` uses German (`DE`) yields. `FR` has no observations. There are no 20Y or 30Y observations for any country.

| Country | Tenor | First date | Last date | Rows | Gaps >5 business days | Largest gap (business days) |
|---|---|---|---|---:|---:|---:|
| AU | 10Y | 2023-08-21 | 2026-09-29 | 774 | 1 | 20 |
| AU | 1Y | 2023-08-21 | 2026-09-29 | 862 | 1 | 20 |
| AU | 2Y | 2023-08-21 | 2026-09-29 | 775 | 1 | 20 |
| AU | 3Y | 2023-08-21 | 2026-09-29 | 848 | 1 | 20 |
| AU | 5Y | 2023-08-21 | 2026-09-29 | 774 | 1 | 20 |
| CA | 10Y | 2023-08-21 | 2026-09-28 | 770 | 1 | 21 |
| CA | 1M | 2023-08-21 | 2026-09-28 | 758 | 1 | 21 |
| CA | 1Y | 2023-08-21 | 2026-09-28 | 758 | 1 | 21 |
| CA | 2Y | 2023-08-21 | 2026-09-28 | 770 | 1 | 21 |
| CA | 3M | 2023-11-06 | 2026-09-28 | 726 | 1 | 21 |
| CA | 3Y | 2023-08-21 | 2026-09-28 | 754 | 1 | 21 |
| CA | 5Y | 2023-08-21 | 2026-09-28 | 769 | 1 | 21 |
| CA | 6M | 2023-08-21 | 2026-09-28 | 760 | 1 | 21 |
| CH | 10Y | 2023-08-21 | 2026-09-29 | 797 | 1 | 20 |
| CH | 2Y | 2023-08-21 | 2026-09-29 | 798 | 1 | 20 |
| CH | 3M | 2023-08-21 | 2026-09-29 | 847 | 1 | 20 |
| DE | 10Y | 2023-08-21 | 2026-09-29 | 789 | 1 | 20 |
| DE | 1Y | 2023-08-21 | 2026-09-29 | 789 | 1 | 20 |
| DE | 2Y | 2023-08-21 | 2026-09-29 | 789 | 1 | 20 |
| DE | 3M | 2023-08-21 | 2026-09-29 | 673 | 3 | 21 |
| DE | 3Y | 2023-08-21 | 2026-09-29 | 789 | 1 | 20 |
| DE | 5Y | 2023-08-21 | 2026-09-29 | 789 | 1 | 20 |
| DE | 6M | 2023-08-21 | 2026-09-29 | 669 | 3 | 24 |
| JP | 10Y | 2023-08-21 | 2026-09-29 | 823 | 1 | 20 |
| JP | 1M | 2023-08-21 | 2026-09-29 | 699 | 1 | 24 |
| JP | 1Y | 2023-08-21 | 2026-09-29 | 828 | 1 | 20 |
| JP | 2Y | 2023-08-21 | 2026-09-29 | 763 | 1 | 20 |
| JP | 3M | 2023-08-21 | 2026-09-29 | 778 | 1 | 23 |
| JP | 3Y | 2023-08-21 | 2026-09-29 | 822 | 1 | 20 |
| JP | 5Y | 2023-08-21 | 2026-09-29 | 823 | 1 | 20 |
| JP | 6M | 2023-08-21 | 2026-09-29 | 822 | 1 | 20 |
| NZ | 10Y | 2023-08-21 | 2026-09-29 | 818 | 1 | 20 |
| NZ | 2Y | 2023-08-21 | 2026-09-29 | 768 | 1 | 20 |
| NZ | 5Y | 2023-08-21 | 2026-09-29 | 776 | 1 | 20 |
| UK | 10Y | 2023-08-21 | 2026-09-29 | 791 | 1 | 20 |
| UK | 1M | 2023-08-21 | 2026-09-28 | 770 | 1 | 21 |
| UK | 1Y | 2023-08-21 | 2026-09-29 | 824 | 1 | 20 |
| UK | 2Y | 2023-08-21 | 2026-09-29 | 812 | 1 | 20 |
| UK | 3M | 2023-08-21 | 2026-09-29 | 775 | 1 | 20 |
| UK | 3Y | 2023-08-21 | 2026-09-29 | 816 | 1 | 20 |
| UK | 5Y | 2023-08-21 | 2026-09-29 | 810 | 1 | 20 |
| UK | 6M | 2023-08-21 | 2026-09-29 | 773 | 1 | 20 |
| US | 10Y | 2023-08-21 | 2026-09-29 | 763 | 1 | 20 |
| US | 1M | 2023-08-21 | 2026-09-29 | 759 | 1 | 20 |
| US | 1Y | 2023-08-21 | 2026-09-29 | 761 | 1 | 20 |
| US | 2Y | 2023-08-21 | 2026-09-29 | 762 | 1 | 20 |
| US | 3M | 2023-08-21 | 2026-09-29 | 801 | 1 | 20 |
| US | 3Y | 2023-08-21 | 2026-09-29 | 800 | 1 | 20 |
| US | 5Y | 2023-08-21 | 2026-09-29 | 773 | 1 | 20 |
| US | 6M | 2023-08-21 | 2026-09-29 | 760 | 1 | 20 |

The dominant gap in every populated series is late August to late September 2026: usually 2026-08-21 to 2026-09-21, with 20 missing business days. CA starts 2026-08-20 (21 days). Some short-tenor exceptions are longer: DE 6M has 24, JP 1M has 24, and JP 3M has 23. DE 3M additionally has 2025-09-26 to 2025-10-09 (8 days) and 2026-07-03 to 2026-07-15 (7 days); DE 6M additionally has 2025-09-26 to 2025-10-10 and 2025-12-15 to 2025-12-29 (9 days each). The September gap is far beyond the proposed two-business-day spread fill and must remain unfilled.

The yield checkpoint marks 72 requested symbol/window keys done for 2023-08-21 through 2026-08-21, but this **does not mean complete usable coverage**. The configured backfill targets only eight maturities (1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 10Y) and eight provider prefixes (US, DE, UK, JP, AU, NZ, CA, SW→CH). FR and 20Y/30Y were never targeted. Fourteen configured short-tenor symbols have no rows; the incremental status lists DE1M, AU1M/3M/6M, NZ1M/3M/6M/1Y/3Y, and SW1M/6M/1Y/3Y/5Y as missing. The latest yield incremental status is `partial` on 2026-09-29; its stale check is also `partial`.

## FX spot

The table has 14 of the requested 28 G10 pairs. The checkpoint marks all **14 configured** pairs done through 2026-08-21. None of the populated pairs has a gap greater than five business days. No pair has observations after 2026-08-21.

| Pair | First date | Last date | Rows | Gaps >5 business days | Largest gap (business days) |
|---|---|---|---:|---:|---:|
| AUD/JPY | 2023-08-21 | 2026-08-21 | 852 | 0 | 0 |
| AUD/NZD | 2023-08-21 | 2026-08-21 | 852 | 0 | 0 |
| AUD/USD | 2023-08-21 | 2026-08-21 | 985 | 0 | 0 |
| CAD/JPY | 2023-08-21 | 2026-08-21 | 851 | 0 | 0 |
| EUR/CHF | 2023-08-21 | 2026-08-21 | 854 | 0 | 0 |
| EUR/GBP | 2023-08-21 | 2026-08-21 | 851 | 0 | 0 |
| EUR/JPY | 2023-08-21 | 2026-08-21 | 854 | 0 | 0 |
| EUR/USD | 2023-08-21 | 2026-08-21 | 1008 | 0 | 0 |
| GBP/JPY | 2023-08-21 | 2026-08-21 | 851 | 0 | 0 |
| GBP/USD | 2023-08-21 | 2026-08-21 | 1018 | 0 | 0 |
| NZD/USD | 2023-08-21 | 2026-08-21 | 892 | 0 | 0 |
| USD/CAD | 2023-08-21 | 2026-08-21 | 852 | 0 | 0 |
| USD/CHF | 2023-08-21 | 2026-08-21 | 844 | 0 | 0 |
| USD/JPY | 2023-08-21 | 2026-08-21 | 1025 | 0 | 0 |

Missing market-convention pairs: EUR/AUD, EUR/NZD, EUR/CAD; GBP/AUD, GBP/NZD, GBP/CAD, GBP/CHF; AUD/CAD, AUD/CHF; NZD/CAD, NZD/CHF, NZD/JPY; CAD/CHF; CHF/JPY. The current `FX_PAIR_SYMBOLS` config includes only the 14 populated pairs. Part B can define all 28, but Part G cannot produce spot correlations for the missing 14 until spot histories are ingested or a clearly identified cross-rate derivation is approved.

## Policy-rate source

`cb_meetings` contains 99 meeting dates and **no rate column**. `rate_snapshots` contains 1,730 implied meeting-rate snapshots, and `ois_cache` contains 728 market curves; neither is an actual policy-rate record. `config/cb_meetings.yaml` holds static `current_rate` values and disagrees with the latest actual release for six of eight banks. The best DB source for 2Y-minus-policy is the latest actual in `indicator_releases` for each bank's mapped policy indicator, with its observation date retained.

| Bank | Country / indicator | Latest actual rate (%) | Release date | Config current_rate (%) |
|---|---|---:|---|---:|
| FED | US / fed_interest_rate_decision | 4.00 | 2026-09-16 | 3.75 |
| ECB | EU / ecb_deposit_rate | 2.50 | 2026-09-10 | 3.00 |
| BOE | UK / bank_rate | 3.75 | 2026-09-17 | 3.75 |
| BOJ | JP / boj_interest_rate_decision | 1.25 | 2026-09-18 | 0.75 |
| RBA | AU / cash_rate | 4.35 | 2026-08-11 | 3.60 |
| BOC | CA / overnight_rate | 2.25 | 2026-07-15 | 2.25 |
| RBNZ | NZ / official_cash_rate | 2.75 | 2026-09-02 | 2.25 |
| SNB | CH / policy_rate | 0.25 | 2025-03-20 | 0.00 |

The SNB actual is stale by more than a year; the configuration value has no effective date. I would expose the SNB policy spread as unavailable until a current, dated actual is present.

## Coverage gate: stop before Part B

The required FR country is entirely absent, and **all** 30Y country/tenor series are absent. No 20Y series exists either. This triggers the prompt's explicit stop condition.

Proposed fallback for review:

1. Use the available 10Y as the long-tenor substitute for regime changes only, explicitly returning `long_tenor: 10Y`. Keep 10s30s and 30Y pair spreads unavailable; do not label a 10Y value as 30Y.
2. Keep the named FR−DE (OAT–Bund) spread unavailable until dated FR 2Y/10Y observations are ingested. Do not proxy France with Germany, which would fabricate a zero spread.
3. Ingest the 14 missing spot pairs, or approve cross-rate derivation with provenance, before promising correlations for all 28 pairs.
4. Investigate the August–September yield gap as a backfill issue. The specified spread fill limit must not bridge it.
5. Use dated policy indicator actuals, and leave stale SNB 2Y-minus-policy unavailable until updated.

No Parts B–H code or migration has been started.
