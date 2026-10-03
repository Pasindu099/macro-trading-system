# Null-period release investigation — 2026-10-03

## Three-level dedup key diff

Read-only command: `docker compose exec app python -m scripts.diff_event_innovation_keys`.

| Measure | Result |
|---|---:|
| Rows whose key changes | 0 |
| Indicators with changed keys | 0 |
| Old deduplicated prints | 17,734 |
| New deduplicated prints | 17,734 |
| Net collapsed prints | **0** |

There are currently no rows that have `period` but lack `period_start_date`, so the new middle fallback does not change any current identity. The 887 scored-eligible rows across 23 indicators with both fields null keep their existing release-date key.

## How ingestion matches both-null prints

`IngestService._upsert_release` in `app/ingestion/ingest_service.py` selects the latest row by `(indicator_id, period)` when `period_start_date` is null. SQLAlchemy translates `period == None` into `period IS NULL`. There is no release-date condition. Therefore a new print with both period fields null can match a previous print for the same indicator. If actual, estimate and previous are equal, ingestion skips it; otherwise it flips one matched row to `is_latest=False` and inserts a new row. A distinct release date is **not** treated as a distinct print by this path. Multiple latest rows already exist for some indicators, so the query's `ORDER BY id DESC LIMIT 1` flips only one of them. This is a separate ingestion issue; no ingestion code was changed here.

## Indicators and counts

Read-only command: `docker compose exec app python -m scripts.report_null_periods`. Targets have at least one mapped, actual-bearing release with both period fields null. Total/latest count all rows of that indicator; null counts cover only rows with both period fields null.

| Country | Indicator | Frequency | Total rows | Latest rows | Null-period rows | Null-period latest | Distinct release dates |
|---|---|---|---:|---:|---:|---:|---:|
| AU | cash_rate | irregular | 69 | 28 | 42 | 6 | 42 |
| AU | private_sector_credit_mom | monthly | 87 | 81 | 2 | 1 | 2 |
| AU | retail_sales_mom | monthly | 114 | 114 | 3 | 3 | 3 |
| AU | td_mi_inflation_gauge_mom | monthly | 100 | 96 | 14 | 14 | 14 |
| CA | overnight_rate | irregular | 101 | 5 | 101 | 5 | 44 |
| CA | vehicle_sales_mom | monthly | 41 | 40 | 15 | 15 | 15 |
| CA | wholesale_sales_mom | monthly | 160 | 153 | 15 | 14 | 15 |
| CH | policy_rate | irregular | 18 | 2 | 18 | 2 | 18 |
| EU | ecb_deposit_rate | irregular | 49 | 6 | 47 | 4 | 33 |
| EU | ecb_marginal_lending_rate | irregular | 2 | 1 | 2 | 1 | 2 |
| JP | adjusted_trade_balance | monthly | 56 | 4 | 56 | 4 | 8 |
| JP | balance_of_trade | monthly | 90 | 87 | 5 | 5 | 5 |
| JP | boj_interest_rate_decision | irregular | 78 | 53 | 78 | 53 | 56 |
| JP | machine_tool_orders_yoy | monthly | 94 | 91 | 8 | 8 | 8 |
| NZ | global_dairy_trade_price_index | irregular | 324 | 86 | 252 | 15 | 151 |
| NZ | imports | monthly | 93 | 83 | 7 | 2 | 7 |
| NZ | milk_auctions | irregular | 134 | 8 | 134 | 8 | 23 |
| NZ | official_cash_rate | irregular | 41 | 4 | 41 | 4 | 29 |
| UK | bank_rate | irregular | 83 | 25 | 61 | 6 | 45 |
| UK | house_price_index_mom | monthly | 100 | 99 | 13 | 13 | 13 |
| UK | house_price_index_yoy | monthly | 190 | 105 | 90 | 8 | 28 |
| US | avg_hourly_earnings_level | monthly | 6 | 4 | 6 | 4 | 6 |
| US | fed_interest_rate_decision | irregular | 68 | 31 | 68 | 31 | 38 |

`is_latest` counts vary widely, including multiple true rows among both-null releases. These counts show the effect is not uniform across existing data; they do not by themselves prove which individual ingestion run set each flag.
