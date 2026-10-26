# Whiteboard Step 1 checkpoint (2026-10-26)

- `0047_whiteboards`: upgrade, downgrade, upgrade clean; `alembic current` showed `0047_whiteboards (head)` before 0048.
- `whiteboards`: `UNIQUE (currency, name)` confirmed.
- `whiteboard_items`: kind CHECK confirmed for `widget`, `note`, `thesis`.
- All four foreign keys on items and links end in `ON DELETE CASCADE`.
- `0048_whiteboard_currencies`: currency CHECK confirmed for USD, EUR, GBP, JPY, CHF, CAD, AUD, NZD; downgrade and upgrade clean; current is `0048_whiteboard_currencies (head)`.
- `SUPPORTED_CURRENCIES` is defined in `app/settings.py:18`. API validation and Main-board creation belong to the pending API/page step.

## Released USD indicator coverage

- Distinct canonical indicators with at least one `indicator_releases` row: **79**.
- Covered by `CB_WATCHLIST['US']`: **9**. Uncovered: **70**, assigned to the future `Other` library group.
- Uncovered (1): adp_employment_change, average_weekly_hours, avg_hourly_earnings_level, avg_hourly_earnings_mom, building_permits, challenger_job_cuts, consumer_inflation_expectations, continuing_jobless_claims, core_cpi_index, core_cpi_mom.
- Uncovered (2): core_pce_price_index_mom, core_pce_prices_qoq, core_ppi_mom, core_ppi_yoy, cpi_headline_mom, cpi_headline_yoy, cpi_index, cpi_sa_index, durable_goods_ex_transport_mom, economic_optimism_index.
- Uncovered (3): employment_cost_benefits_qoq, employment_cost_index_qoq, employment_cost_wages_qoq, export_prices_mom, export_prices_yoy, fed_balance_sheet, fed_interest_rate_decision, government_payrolls, import_prices_mom, import_prices_yoy, imports, inflation_expectations.
- Uncovered (4): ism_manufacturing_employment, ism_manufacturing_new_orders, ism_manufacturing_prices, ism_services_employment, ism_services_new_orders, ism_services_pmi, ism_services_prices, jobless_claims_4week_average, manufacturing_payrolls, michigan_inflation_expectations.
- Uncovered (5): new_home_sales, nfib_business_optimism, ny_empire_state_mfg, participation_rate, pce_price_index_mom, pce_price_index_yoy, pce_prices_qoq, philly_fed_mfg, ppi_ex_food_energy_trade_mom, ppi_ex_food_energy_trade_yoy, ppi_index, ppi_mom, ppi_yoy.
- Uncovered (6): private_nonfarm_payrolls, real_gdp_qoq_annualised, redbook_yoy, retail_inventories_ex_autos_mom, retail_sales_yoy, sp_global_composite_pmi, sp_global_manufacturing_pmi, sp_global_services_pmi, u6_unemployment_rate.
- Uncovered (7): umich_consumer_expectations, umich_consumer_sentiment, umich_current_conditions, umich_inflation_exp_1y, umich_inflation_exp_5y, wholesale_inventories_mom.

## Currency and country mapping

| Currency | `CB_WATCHLIST` key | Local `countries` rows | Notes |
|---|---|---|---|
| USD | US | US | |
| EUR | EU | DE, EU, FR | Watchlist has EU entries, including one DE-sourced item; FR releases still need `Other`. |
| GBP | UK | UK | |
| JPY | JP | JP | |
| CHF | CH | CH | |
| CAD | CA | CA | |
| AUD | AU | AU | Desk currently disabled in `config/desks.yaml`. |
| NZD | NZ | NZ | Desk currently disabled in `config/desks.yaml`. |

`CB_WATCHLIST` was not edited. The library must read release indicators for every country mapped to the currency and put those absent from the matching watchlist key in `Other`.
