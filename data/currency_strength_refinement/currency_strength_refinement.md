# Currency Strength Weight Refinement

## Purpose

This document explains the refinement of initial macro indicator weights for the currency strength model. The process starts from the correlation/PCA/model-importance weights generated from `target_correlations.html` and `indicator_weights.html`, then validates those weights against next-month FX returns.

## Initial Correlation Analysis

The initial weighting process identified inflation, GDP/growth, and unemployment target proxies for each central bank. Each candidate indicator was scored using Pearson/Spearman correlation to those targets, PCA loading strength, model feature-importance evidence, data coverage, and same-theme economic relevance.

## Economic Significance

- Growth indicators are generally currency-positive when stronger because they imply better activity and tighter policy expectations.
- Labor strength is generally currency-positive; unemployment-linked indicators are usually currency-negative when they rise.
- Inflation is regime-dependent: moderate upside inflation can support the currency through policy tightening expectations, while excessive inflation can hurt real growth and credibility.

## Preliminary Currency Strength Model

A monthly macro strength score was built by multiplying normalized indicator values by their signed weights and summing by currency. The score was evaluated against next-month FX returns versus USD. For USD-quoted inverse pairs such as USDCAD, USDCHF, and USDJPY, returns were inverted so positive return always means local-currency strength.

### Validation Summary

| central_bank_code | currency | pearson_ic_initial | pearson_ic_refined | pearson_ic_change | directional_hit_rate_initial | directional_hit_rate_refined | hit_rate_change |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BOC | CAD | -0.1703 | -0.1530 | 0.0173 | 0.4667 | 0.5067 | 0.0400 |
| BOE | GBP | -0.1149 | -0.1098 | 0.0050 | 0.4400 | 0.4533 | 0.0133 |
| BOJ | JPY | -0.0009 | 0.0029 | 0.0038 | 0.4667 | 0.4667 | 0.0000 |
| ECB | EUR | -0.0306 | -0.0058 | 0.0248 | 0.4800 | 0.5200 | 0.0400 |
| FED | USD | 0.2055 | 0.2032 | -0.0024 | 0.5085 | 0.5085 | 0.0000 |
| RBA | AUD | -0.1335 | -0.1384 | -0.0049 | 0.4533 | 0.4667 | 0.0133 |
| RBNZ | NZD | -0.0972 | -0.0969 | 0.0003 | 0.5541 | 0.5541 | 0.0000 |
| SNB | CHF | -0.0575 | -0.0540 | 0.0035 | 0.4595 | 0.4459 | -0.0135 |

## Iterative Refinement

Each indicator contribution was validated against next-month FX returns. Indicators with positive contribution alignment received a moderate weight increase, while indicators that moved against returns were reduced. Changes were clipped to avoid overfitting a short validation sample.

## Recommended Indicator Subset

The recommended subset keeps the top indicators by refined normalized weight for each central bank. This simplifies the model, improves interpretability, and reduces exposure to low-weight redundant indicators.

| central_bank_code | currency | indicator_key | indicator | indicator_category | refined_normalized_abs_weight | refinement_rationale |
| --- | --- | --- | --- | --- | --- | --- |
| BOC | CAD | cpi_headline_yoy | Headline CPI (YoY) | Inflation | 0.0693 | Mostly unchanged: validation signal was neutral. |
| BOC | CAD | full_time_employment_change | Full-Time Employment Change | Labor | 0.0582 | Mostly unchanged: validation signal was neutral. |
| BOC | CAD | employment_change | Employment Change | Labor | 0.0567 | Mostly unchanged: validation signal was neutral. |
| BOC | CAD | retail_sales_yoy | Retail Sales (YoY) | Growth | 0.0501 | Mostly unchanged: validation signal was neutral. |
| BOC | CAD | ppi_yoy | PPI (YoY) | Inflation | 0.0496 | Mostly unchanged: validation signal was neutral. |
| BOC | CAD | cpi_headline_mom | Headline CPI (MoM) | Inflation | 0.0496 | Mostly unchanged: validation signal was neutral. |
| BOC | CAD | composite_pmi | Composite PMI | Growth | 0.0484 | Increased: contribution aligned with forward FX returns. |
| BOC | CAD | services_pmi | Services PMI | Growth | 0.0466 | Increased: contribution aligned with forward FX returns. |
| BOC | CAD | ivey_pmi_sa | Ivey PMI s.a. | Growth | 0.0458 | Mostly unchanged: validation signal was neutral. |
| BOC | CAD | raw_materials_prices_yoy | Raw Materials Prices (YoY) | Inflation | 0.0457 | Mostly unchanged: validation signal was neutral. |
| BOC | CAD | manufacturing_pmi | Manufacturing PMI | Growth | 0.0429 | Reduced: contribution moved against forward FX returns. |
| BOC | CAD | balance_of_trade | Balance of Trade | Trade | 0.0421 | Reduced: contribution moved against forward FX returns. |
| BOE | GBP | retail_price_index_yoy | Retail Price Index (YoY) | Inflation | 0.0656 | Mostly unchanged: validation signal was neutral. |
| BOE | GBP | cpi_headline_yoy | Headline CPI (YoY) | Inflation | 0.0620 | Mostly unchanged: validation signal was neutral. |
| BOE | GBP | avg_earnings_excl_bonus | Average Earnings (excl. Bonus) | Labor | 0.0561 | Mostly unchanged: validation signal was neutral. |
| BOE | GBP | avg_earnings_incl_bonus | Average Earnings (incl. Bonus) | Labor | 0.0557 | Mostly unchanged: validation signal was neutral. |
| BOE | GBP | ppi_output_yoy | PPI Output (YoY) | Inflation | 0.0511 | Reduced: contribution moved against forward FX returns. |
| BOE | GBP | ppi_core_output_yoy | PPI Core Output (YoY) | Inflation | 0.0507 | Reduced: contribution moved against forward FX returns. |
| BOE | GBP | ppi_input_yoy | PPI Input (YoY) | Inflation | 0.0484 | Reduced: contribution moved against forward FX returns. |
| BOE | GBP | retail_price_index_mom | Retail Price Index (MoM) | Inflation | 0.0480 | Mostly unchanged: validation signal was neutral. |
| BOE | GBP | cbi_industrial_trends_orders | CBI Industrial Trends Orders | Sentiment | 0.0454 | Reduced: contribution moved against forward FX returns. |
| BOE | GBP | ppi_core_output_mom | PPI Core Output (MoM) | Inflation | 0.0405 | Reduced: contribution moved against forward FX returns. |
| BOE | GBP | cpi_headline_mom | Headline CPI (MoM) | Inflation | 0.0388 | Reduced: contribution moved against forward FX returns. |
| BOE | GBP | employment_change | Employment Change | Labor | 0.0350 | Mostly unchanged: validation signal was neutral. |
| BOJ | JPY | coincident_index | Coincident Index | Growth | 0.0559 | Mostly unchanged: validation signal was neutral. |
| BOJ | JPY | cpi_ex_food_energy_yoy | CPI Ex Food and Energy (YoY) | Inflation | 0.0553 | Mostly unchanged: validation signal was neutral. |
| BOJ | JPY | services_pmi | S&P Global Services PMI | Growth | 0.0533 | Mostly unchanged: validation signal was neutral. |
| BOJ | JPY | cpi_index | Consumer Price Index | Inflation | 0.0505 | Mostly unchanged: validation signal was neutral. |
| BOJ | JPY | composite_pmi | S&P Global Composite PMI | Growth | 0.0444 | Reduced: contribution moved against forward FX returns. |
| BOJ | JPY | retail_sales_yoy | Retail Sales (YoY) | Growth | 0.0423 | Mostly unchanged: validation signal was neutral. |
| BOJ | JPY | leading_economic_index | Leading Economic Index | Growth | 0.0407 | Mostly unchanged: validation signal was neutral. |
| BOJ | JPY | eco_watchers_current | Eco Watchers Survey Current | Sentiment | 0.0372 | Reduced: contribution moved against forward FX returns. |
| BOJ | JPY | core_cpi_yoy | Core CPI (YoY) | Inflation | 0.0360 | Mostly unchanged: validation signal was neutral. |
| BOJ | JPY | tankan_large_non_manufacturing_index | Tankan Large Non-Manufacturing Index | Sentiment | 0.0358 | Reduced: contribution moved against forward FX returns. |
| BOJ | JPY | ppi_yoy | PPI (YoY) | Inflation | 0.0357 | Mostly unchanged: validation signal was neutral. |
| BOJ | JPY | tankan_non_manufacturing_outlook | Tankan Non-Manufacturing Outlook | Sentiment | 0.0345 | Reduced: contribution moved against forward FX returns. |
| ECB | EUR | cpi_headline_yoy | Headline HICP (YoY) | Inflation | 0.2218 | Reduced: contribution moved against forward FX returns. |
| ECB | EUR | core_cpi_yoy | Core HICP (YoY) | Inflation | 0.1381 | Mostly unchanged: validation signal was neutral. |
| ECB | EUR | construction_output_yoy | Construction Output (YoY) | Growth | 0.1162 | Mostly unchanged: validation signal was neutral. |
| ECB | EUR | consumer_inflation_expectation | Consumer Inflation Expectation | Inflation | 0.1131 | Increased: contribution aligned with forward FX returns. |
| ECB | EUR | zew_economic_sentiment | ZEW Economic Sentiment Index | Sentiment | 0.0886 | Mostly unchanged: validation signal was neutral. |
| ECB | EUR | economic_sentiment | Economic Sentiment | Sentiment | 0.0632 | Reduced: contribution moved against forward FX returns. |
| ECB | EUR | consumer_confidence | Consumer Confidence | Sentiment | 0.0555 | Mostly unchanged: validation signal was neutral. |
| ECB | EUR | cpi_headline_mom | Headline HICP (MoM) | Inflation | 0.0462 | Reduced: contribution moved against forward FX returns. |
| ECB | EUR | composite_pmi | Composite PMI | Growth | 0.0438 | Increased: contribution aligned with forward FX returns. |
| ECB | EUR | services_pmi | Services PMI | Growth | 0.0380 | Increased: contribution aligned with forward FX returns. |
| ECB | EUR | unemployment_rate | Unemployment Rate | Labor | 0.0290 | Mostly unchanged: validation signal was neutral. |
| ECB | EUR | cpi_index | Consumer Price Index | Inflation | 0.0201 | Increased: contribution aligned with forward FX returns. |
| FED | USD | core_pce_price_index_yoy | Core PCE Price Index (YoY) | Inflation | 0.0410 | Increased: contribution aligned with forward FX returns. |
| FED | USD | pce_price_index_yoy | PCE Price Index (YoY) | Inflation | 0.0387 | Increased: contribution aligned with forward FX returns. |
| FED | USD | challenger_job_cuts | Challenger Job Cuts | Labor | 0.0387 | Increased: contribution aligned with forward FX returns. |
| FED | USD | avg_hourly_earnings_mom | Average Hourly Earnings (MoM) | Labor | 0.0347 | Increased: contribution aligned with forward FX returns. |
| FED | USD | cpi_headline_yoy | Headline CPI (YoY) | Inflation | 0.0342 | Mostly unchanged: validation signal was neutral. |
| FED | USD | ism_manufacturing_employment | ISM Manufacturing Employment | Growth | 0.0328 | Increased: contribution aligned with forward FX returns. |
| FED | USD | adp_employment_change | ADP Employment Change | Labor | 0.0311 | Increased: contribution aligned with forward FX returns. |
| FED | USD | core_ppi_yoy | Core PPI (YoY) | Inflation | 0.0310 | Mostly unchanged: validation signal was neutral. |
| FED | USD | government_payrolls | Government Payrolls | Labor | 0.0307 | Mostly unchanged: validation signal was neutral. |
| FED | USD | consumer_inflation_expectations | Consumer Inflation Expectations | Inflation | 0.0307 | Mostly unchanged: validation signal was neutral. |
| FED | USD | ppi_yoy | PPI (YoY) | Inflation | 0.0300 | Mostly unchanged: validation signal was neutral. |
| FED | USD | manufacturing_payrolls | Manufacturing Payrolls | Labor | 0.0295 | Mostly unchanged: validation signal was neutral. |
| RBA | AUD | cpi_headline_qoq | Headline CPI (QoQ) | Inflation | 0.0582 | Mostly unchanged: validation signal was neutral. |
| RBA | AUD | rba_trimmed_mean_cpi_qoq | RBA Trimmed Mean CPI (QoQ) | Inflation | 0.0538 | Reduced: contribution moved against forward FX returns. |
| RBA | AUD | rba_weighted_median_cpi_yoy | RBA Weighted Median CPI (YoY) | Inflation | 0.0525 | Reduced: contribution moved against forward FX returns. |
| RBA | AUD | producer_price_index_yoy | Producer Price Index (YoY) | Inflation | 0.0524 | Reduced: contribution moved against forward FX returns. |
| RBA | AUD | producer_price_index_qoq | Producer Price Index (QoQ) | Inflation | 0.0521 | Reduced: contribution moved against forward FX returns. |
| RBA | AUD | rba_weighted_median_cpi_qoq | RBA Weighted Median CPI (QoQ) | Inflation | 0.0507 | Reduced: contribution moved against forward FX returns. |
| RBA | AUD | rba_trimmed_mean_cpi_yoy | RBA Trimmed Mean CPI (YoY) | Inflation | 0.0454 | Reduced: contribution moved against forward FX returns. |
| RBA | AUD | commodity_prices_yoy | Commodity Prices (YoY) | Inflation | 0.0376 | Mostly unchanged: validation signal was neutral. |
| RBA | AUD | manufacturing_pmi | Manufacturing PMI | Growth | 0.0371 | Mostly unchanged: validation signal was neutral. |
| RBA | AUD | consumer_inflation_expectation | Consumer Inflation Expectation | Inflation | 0.0368 | Mostly unchanged: validation signal was neutral. |
| RBA | AUD | services_pmi | Services PMI | Growth | 0.0367 | Mostly unchanged: validation signal was neutral. |
| RBA | AUD | nab_business_confidence | NAB Business Confidence | Sentiment | 0.0360 | Mostly unchanged: validation signal was neutral. |
| RBNZ | NZD | labour_cost_index_qoq | Labour Cost Index (QoQ) | Labor | 0.1467 | Reduced: contribution moved against forward FX returns. |
| RBNZ | NZD | cpi_headline_qoq | Headline CPI (QoQ) | Inflation | 0.1385 | Mostly unchanged: validation signal was neutral. |
| RBNZ | NZD | labour_cost_index_yoy | Labour Cost Index (YoY) | Labor | 0.1260 | Reduced: contribution moved against forward FX returns. |
| RBNZ | NZD | balance_of_trade | Balance of Trade | Growth | 0.1178 | Mostly unchanged: validation signal was neutral. |
| RBNZ | NZD | participation_rate | Labor Force Participation Rate | Labor | 0.1174 | Mostly unchanged: validation signal was neutral. |
| RBNZ | NZD | exports | Exports | Growth | 0.1036 | Mostly unchanged: validation signal was neutral. |
| RBNZ | NZD | employment_change_qoq | Employment Change (QoQ) | Labor | 0.0876 | Mostly unchanged: validation signal was neutral. |
| RBNZ | NZD | business_confidence | Business Confidence | Growth | 0.0650 | Mostly unchanged: validation signal was neutral. |

## Sensitivity Analysis

For the largest refined-weight indicators, weights were shocked by +/-20%. The resulting changes in Pearson information coefficient and directional hit rate identify which indicators most influence validation performance.

| central_bank_code | currency | indicator_key | indicator | shock | baseline_pearson_ic | scenario_pearson_ic | pearson_ic_delta | baseline_hit_rate | scenario_hit_rate | hit_rate_delta | sensitivity_magnitude |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ECB | EUR | zew_economic_sentiment | ZEW Economic Sentiment Index | minus_20pct | -0.0058 | -0.0040 | 0.0019 | 0.5200 | 0.5467 | 0.0267 | 0.0285 |
| RBNZ | NZD | credit_card_spending_yoy | Credit Card Spending (YoY) | plus_20pct | -0.0969 | -0.0974 | -0.0005 | 0.5541 | 0.5811 | 0.0270 | 0.0275 |
| RBA | AUD | commodity_prices_yoy | Commodity Prices (YoY) | plus_20pct | -0.1384 | -0.1385 | -0.0001 | 0.4667 | 0.4400 | -0.0267 | 0.0267 |
| ECB | EUR | consumer_inflation_expectation | Consumer Inflation Expectation | plus_20pct | -0.0058 | 0.0016 | 0.0074 | 0.5200 | 0.5333 | 0.0133 | 0.0207 |
| ECB | EUR | consumer_inflation_expectation | Consumer Inflation Expectation | minus_20pct | -0.0058 | -0.0131 | -0.0073 | 0.5200 | 0.5067 | -0.0133 | 0.0206 |
| ECB | EUR | cpi_headline_yoy | Headline HICP (YoY) | plus_20pct | -0.0058 | -0.0120 | -0.0062 | 0.5200 | 0.5067 | -0.0133 | 0.0195 |
| RBNZ | NZD | exports | Exports | minus_20pct | -0.0969 | -0.1009 | -0.0040 | 0.5541 | 0.5676 | 0.0135 | 0.0176 |
| BOC | CAD | composite_pmi | Composite PMI | plus_20pct | -0.1530 | -0.1500 | 0.0030 | 0.5067 | 0.5200 | 0.0133 | 0.0163 |
| BOC | CAD | services_pmi | Services PMI | plus_20pct | -0.1530 | -0.1504 | 0.0026 | 0.5067 | 0.5200 | 0.0133 | 0.0159 |
| BOE | GBP | cbi_industrial_trends_orders | CBI Industrial Trends Orders | plus_20pct | -0.1098 | -0.1122 | -0.0024 | 0.4533 | 0.4400 | -0.0133 | 0.0157 |
| BOE | GBP | avg_earnings_excl_bonus | Average Earnings (excl. Bonus) | minus_20pct | -0.1098 | -0.1122 | -0.0024 | 0.4533 | 0.4400 | -0.0133 | 0.0157 |
| SNB | CHF | producer_import_prices_yoy | Producer & Import Prices (YoY) | minus_20pct | -0.0540 | -0.0518 | 0.0022 | 0.4459 | 0.4324 | -0.0135 | 0.0157 |
| BOC | CAD | cpi_headline_yoy | Headline CPI (YoY) | plus_20pct | -0.1530 | -0.1552 | -0.0022 | 0.5067 | 0.5200 | 0.0133 | 0.0156 |
| BOC | CAD | retail_sales_yoy | Retail Sales (YoY) | plus_20pct | -0.1530 | -0.1509 | 0.0021 | 0.5067 | 0.5200 | 0.0133 | 0.0154 |
| SNB | CHF | producer_import_prices_yoy | Producer & Import Prices (YoY) | plus_20pct | -0.0540 | -0.0559 | -0.0019 | 0.4459 | 0.4595 | 0.0135 | 0.0154 |
| RBNZ | NZD | business_confidence | Business Confidence | plus_20pct | -0.0969 | -0.0954 | 0.0015 | 0.5541 | 0.5676 | 0.0135 | 0.0150 |
| RBNZ | NZD | labour_cost_index_yoy | Labour Cost Index (YoY) | minus_20pct | -0.0969 | -0.0956 | 0.0013 | 0.5541 | 0.5676 | 0.0135 | 0.0149 |
| BOE | GBP | ppi_core_output_yoy | PPI Core Output (YoY) | plus_20pct | -0.1098 | -0.1112 | -0.0014 | 0.4533 | 0.4400 | -0.0133 | 0.0147 |
| BOE | GBP | avg_earnings_incl_bonus | Average Earnings (incl. Bonus) | minus_20pct | -0.1098 | -0.1085 | 0.0013 | 0.4533 | 0.4400 | -0.0133 | 0.0147 |
| BOC | CAD | cpi_headline_mom | Headline CPI (MoM) | plus_20pct | -0.1530 | -0.1516 | 0.0013 | 0.5067 | 0.5200 | 0.0133 | 0.0147 |
| BOC | CAD | raw_materials_prices_yoy | Raw Materials Prices (YoY) | minus_20pct | -0.1530 | -0.1540 | -0.0010 | 0.5067 | 0.5200 | 0.0133 | 0.0144 |
| ECB | EUR | core_cpi_yoy | Core HICP (YoY) | minus_20pct | -0.0058 | -0.0067 | -0.0009 | 0.5200 | 0.5333 | 0.0133 | 0.0142 |
| BOE | GBP | retail_price_index_yoy | Retail Price Index (YoY) | plus_20pct | -0.1098 | -0.1091 | 0.0008 | 0.4533 | 0.4400 | -0.0133 | 0.0141 |
| BOE | GBP | ppi_input_yoy | PPI Input (YoY) | plus_20pct | -0.1098 | -0.1106 | -0.0007 | 0.4533 | 0.4400 | -0.0133 | 0.0141 |
| BOE | GBP | ppi_output_yoy | PPI Output (YoY) | plus_20pct | -0.1098 | -0.1105 | -0.0007 | 0.4533 | 0.4400 | -0.0133 | 0.0140 |
| BOE | GBP | cpi_headline_yoy | Headline CPI (YoY) | plus_20pct | -0.1098 | -0.1093 | 0.0006 | 0.4533 | 0.4400 | -0.0133 | 0.0139 |
| SNB | CHF | producer_import_prices_mom | Producer & Import Prices (MoM) | plus_20pct | -0.0540 | -0.0544 | -0.0004 | 0.4459 | 0.4595 | 0.0135 | 0.0139 |
| RBA | AUD | manufacturing_pmi | Manufacturing PMI | minus_20pct | -0.1384 | -0.1379 | 0.0005 | 0.4667 | 0.4533 | -0.0133 | 0.0138 |
| SNB | CHF | balance_of_trade | Balance of Trade | minus_20pct | -0.0540 | -0.0537 | 0.0003 | 0.4459 | 0.4595 | 0.0135 | 0.0138 |
| SNB | CHF | economic_sentiment_index | Economic Sentiment Index | plus_20pct | -0.0540 | -0.0537 | 0.0003 | 0.4459 | 0.4324 | -0.0135 | 0.0138 |
| RBA | AUD | rba_weighted_median_cpi_yoy | RBA Weighted Median CPI (YoY) | plus_20pct | -0.1384 | -0.1389 | -0.0005 | 0.4667 | 0.4800 | 0.0133 | 0.0138 |
| RBA | AUD | manufacturing_pmi | Manufacturing PMI | plus_20pct | -0.1384 | -0.1389 | -0.0005 | 0.4667 | 0.4533 | -0.0133 | 0.0138 |
| BOC | CAD | ppi_yoy | PPI (YoY) | plus_20pct | -0.1530 | -0.1525 | 0.0004 | 0.5067 | 0.5200 | 0.0133 | 0.0138 |
| SNB | CHF | balance_of_trade | Balance of Trade | plus_20pct | -0.0540 | -0.0542 | -0.0002 | 0.4459 | 0.4324 | -0.0135 | 0.0137 |
| ECB | EUR | cpi_headline_mom | Headline HICP (MoM) | minus_20pct | -0.0058 | -0.0057 | 0.0001 | 0.5200 | 0.5333 | 0.0133 | 0.0135 |
| RBA | AUD | commodity_prices_yoy | Commodity Prices (YoY) | minus_20pct | -0.1384 | -0.1383 | 0.0001 | 0.4667 | 0.4800 | 0.0133 | 0.0134 |
| RBA | AUD | consumer_inflation_expectation | Consumer Inflation Expectation | plus_20pct | -0.1384 | -0.1384 | 0.0000 | 0.4667 | 0.4533 | -0.0133 | 0.0134 |
| ECB | EUR | cpi_headline_yoy | Headline HICP (YoY) | minus_20pct | -0.0058 | 0.0014 | 0.0073 | 0.5200 | 0.5200 | 0.0000 | 0.0073 |
| ECB | EUR | economic_sentiment | Economic Sentiment | minus_20pct | -0.0058 | -0.0006 | 0.0052 | 0.5200 | 0.5200 | 0.0000 | 0.0052 |
| ECB | EUR | economic_sentiment | Economic Sentiment | plus_20pct | -0.0058 | -0.0110 | -0.0052 | 0.5200 | 0.5200 | 0.0000 | 0.0052 |

## Final Recommendation

Use `recommended_indicator_subset.csv` as the first production-candidate indicator set and `refined_indicator_weights.csv` as the full research set. Before deployment, rerun validation with strict release-date alignment and a rolling-window backtest against the dashboard's existing currency stance layer.
