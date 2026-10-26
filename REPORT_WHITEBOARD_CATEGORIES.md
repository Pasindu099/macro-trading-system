# Whiteboard category audit and proposal (2026-10-26)

No code or scoring data was changed for this audit.

## Existing metadata

`indicators.primary_category` is required text; `secondary_categories` is a text array (`app/db/models.py:93`). `indicator_releases` points to `indicators` and has no category of its own. Counts below are distinct US canonical indicators with at least one release (79 total).

| Primary category | USD count | Proposed whiteboard category |
|---|---:|---|
| Inflation | 29 | inflation |
| Growth | 21 | growth |
| Labor | 19 | labor |
| Sentiment | 5 | growth |
| Housing | 2 | growth |
| Monetary Policy | 2 | central_bank |
| Trade | 1 | flow |

Secondary categories overlap: none 58, Inflation 8, Sentiment 6, Trade 5, Labor 2. Use `primary_category` for one stable library placement; secondary categories can support search or badges later. Across the eight currencies (including EU/DE/FR), all released indicators use these same seven primary values. Thus a name/code rule file is unnecessary for the current data. A whiteboard-only mapping in `app/whiteboard/categories.py` can translate these seven values to library categories, with unknown/future values falling into `other`. `rates` holds the fixed rate widgets; no current release primary category maps there. `other` has **0 USD indicators** under this proposal.

## Full USD library result (all 79 released indicators)

| Whiteboard category | Count | Canonical indicator codes |
|---|---:|---|
| central_bank | 2 | fed_balance_sheet, fed_interest_rate_decision |
| rates | 0 | — |
| labor | 19 | adp_employment_change, average_weekly_hours, avg_hourly_earnings_level, avg_hourly_earnings_mom, avg_hourly_earnings_yoy, challenger_job_cuts, continuing_jobless_claims, employment_cost_benefits_qoq, employment_cost_index_qoq, employment_cost_wages_qoq, government_payrolls, initial_jobless_claims, jobless_claims_4week_average, manufacturing_payrolls, nfp, participation_rate, private_nonfarm_payrolls, u6_unemployment_rate, unemployment_rate |
| inflation | 29 | consumer_inflation_expectations, core_cpi_index, core_cpi_mom, core_cpi_yoy, core_pce_price_index_mom, core_pce_price_index_yoy, core_pce_prices_qoq, core_ppi_mom, core_ppi_yoy, cpi_headline_mom, cpi_headline_yoy, cpi_index, cpi_sa_index, export_prices_mom, export_prices_yoy, import_prices_mom, import_prices_yoy, inflation_expectations, michigan_inflation_expectations, pce_price_index_mom, pce_price_index_yoy, pce_prices_qoq, ppi_ex_food_energy_trade_mom, ppi_ex_food_energy_trade_yoy, ppi_index, ppi_mom, ppi_yoy, umich_inflation_exp_1y, umich_inflation_exp_5y |
| growth | 28 | building_permits, durable_goods_ex_transport_mom, economic_optimism_index, ism_manufacturing_employment, ism_manufacturing_new_orders, ism_manufacturing_pmi, ism_manufacturing_prices, ism_services_business_activity, ism_services_employment, ism_services_new_orders, ism_services_pmi, ism_services_prices, new_home_sales, nfib_business_optimism, ny_empire_state_mfg, philly_fed_mfg, real_gdp_qoq_annualised, redbook_yoy, retail_inventories_ex_autos_mom, retail_sales_mom, retail_sales_yoy, sp_global_composite_pmi, sp_global_manufacturing_pmi, sp_global_services_pmi, umich_consumer_expectations, umich_consumer_sentiment, umich_current_conditions, wholesale_inventories_mom |
| flow | 1 | imports |
| other | 0 | — |

## Country scope and CB badge

Query `countries` by `currency_code` to include USD=US, EUR=EU+DE+FR, GBP=UK, JPY=JP, CHF=CH, CAD=CA, AUD=AU, NZD=NZ. Do not infer the library's category from `CB_WATCHLIST`. For `cb_preferred`, match `(source_country or watchlist country, canonical_name)` against the indicator row. The EU watchlist includes two DE-sourced entries (`app/processing/cb_preferred_score.py:134,145`). `CB_WATCHLIST` stays unchanged.
