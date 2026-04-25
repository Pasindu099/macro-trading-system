# Production Currency Strength Model

## Model Overview and Objectives

The production-candidate currency strength model converts macroeconomic indicator releases into a normalized strength score for each currency. The objective is to provide an interpretable macro signal that can be compared across AUD, CAD, CHF, EUR, GBP, JPY, NZD, and USD.

## Data Sources and Preprocessing

- `data/eda/eda_observations.csv`: cleaned macro observations with normalized values and release timestamps.
- `data/currency_strength_refinement/recommended_indicator_subset.csv`: production subset selected from refined weights.
- `data/currency_strength_refinement/refined_indicator_weights.csv`: refined signed weights.
- `data/fx_validation/fx_returns.csv`: monthly FX returns used for validation.
- `data/currency_stance/currency_stance.csv`: existing dashboard stance layer used as comparison.

The signal calculation is release-date aligned: an indicator can only affect a signal date if its `release_timestamp_utc` is less than or equal to that signal date.

## Indicator Selection and Weighting

Indicators are selected from the refined subset. Weights are signed: positive weights mean higher indicator values contribute to currency strength, while negative weights mean higher values detract from currency strength. The selected weights are renormalized by currency so the absolute weights sum to 1.

### Selected Indicator Count

| central_bank_code | currency | indicators | abs_weight_sum |
| --- | --- | --- | --- |
| BOC | CAD | 12 | 1.0000 |
| BOE | GBP | 12 | 1.0000 |
| BOJ | JPY | 12 | 1.0000 |
| ECB | EUR | 12 | 1.0000 |
| FED | USD | 12 | 1.0000 |
| RBA | AUD | 12 | 1.0000 |
| RBNZ | NZD | 12 | 1.0000 |
| SNB | CHF | 11 | 1.0000 |

## Assumptions and Limitations

- The model is monthly in this validation pack; intraday/live scoring can reuse the same release-aligned function with a current `as_of` timestamp.
- Normalized indicator values are inherited from the EDA layer. A production hardening step should calculate normalization parameters from training windows only.
- FX validation uses USD pairs and inverts USD-quoted pairs so positive forward return means local-currency strength.
- Inflation effects are regime-dependent and should not be interpreted mechanically.

## Final Validation Results

### Production Candidate Metrics

| model_name | central_bank_code | currency | observations | pearson_ic | spearman_ic | hit_rate | avg_monthly_strategy_return | monthly_strategy_volatility | information_ratio_annualized | max_drawdown | positive_signal_share | avg_abs_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| refined_subset | BOC | CAD | 74 | -0.0907 | -0.0126 | 0.5541 | 0.0013 | 0.0184 | 0.2379 | -0.1356 | 0.3919 | 0.4474 |
| refined_subset | BOE | GBP | 74 | -0.1311 | -0.0850 | 0.4595 | -0.0015 | 0.0228 | -0.2289 | -0.2955 | 0.4054 | 0.5886 |
| refined_subset | BOJ | JPY | 74 | -0.0190 | -0.0188 | 0.4595 | -0.0011 | 0.0287 | -0.1349 | -0.2568 | 0.6216 | 0.6406 |
| refined_subset | ECB | EUR | 74 | 0.0074 | 0.0220 | 0.6216 | 0.0030 | 0.0210 | 0.5002 | -0.1607 | 0.6081 | 0.4118 |
| refined_subset | FED | USD | 72 | 0.1797 | 0.1566 | 0.5417 | 0.0034 | 0.0202 | 0.5829 | -0.0977 | 0.4306 | 0.4909 |
| refined_subset | RBA | AUD | 74 | -0.0791 | -0.0459 | 0.5135 | -0.0004 | 0.0289 | -0.0432 | -0.2367 | 0.5405 | 0.5368 |
| refined_subset | RBNZ | NZD | 74 | -0.0267 | -0.0377 | 0.4865 | -0.0006 | 0.0304 | -0.0703 | -0.3106 | 0.5000 | 0.5349 |
| refined_subset | SNB | CHF | 74 | -0.0429 | -0.0651 | 0.4730 | -0.0014 | 0.0219 | -0.2203 | -0.1684 | 0.4189 | 0.5022 |

### Existing Currency Stance Metrics

| model_name | central_bank_code | currency | observations | pearson_ic | spearman_ic | hit_rate | avg_monthly_strategy_return | monthly_strategy_volatility | information_ratio_annualized | max_drawdown | positive_signal_share | avg_abs_score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| existing_currency_stance | BOC | CAD | 75 | -0.1151 | 0.0264 | 0.4667 | 0.0004 | 0.0183 | 0.0727 | -0.1862 | 0.4400 | 0.9973 |
| existing_currency_stance | BOE | GBP | 74 | 0.1500 | 0.1100 | 0.5541 | 0.0018 | 0.0227 | 0.2776 | -0.1669 | 0.4865 | 0.4888 |
| existing_currency_stance | BOJ | JPY | 75 | -0.0542 | -0.1060 | 0.4533 | -0.0061 | 0.0279 | -0.7545 | -0.4952 | 0.6133 | 0.7269 |
| existing_currency_stance | ECB | EUR | 75 | -0.0441 | -0.0626 | 0.5333 | 0.0008 | 0.0212 | 0.1333 | -0.1689 | 0.6267 | 0.5685 |
| existing_currency_stance | FED | USD | 72 | 0.1976 | 0.1538 | 0.5417 | 0.0012 | 0.0204 | 0.2059 | -0.1072 | 0.4722 | 2.2285 |
| existing_currency_stance | RBA | AUD | 75 | -0.1066 | -0.0259 | 0.5200 | 0.0027 | 0.0286 | 0.3285 | -0.1634 | 0.4400 | 0.5451 |
| existing_currency_stance | RBNZ | NZD | 74 | -0.1547 | -0.1050 | 0.4324 | -0.0017 | 0.0300 | -0.1980 | -0.2588 | 0.4324 | 1.3063 |
| existing_currency_stance | SNB | CHF | 74 | -0.0876 | -0.0612 | 0.5270 | -0.0002 | 0.0220 | -0.0267 | -0.1414 | 0.4459 | 1.0541 |

### Production vs Existing Stance

| model_name_production | central_bank_code | currency | observations_production | pearson_ic_production | spearman_ic_production | hit_rate_production | avg_monthly_strategy_return_production | monthly_strategy_volatility_production | information_ratio_annualized_production | max_drawdown_production | positive_signal_share_production | avg_abs_score_production | model_name_stance | observations_stance | pearson_ic_stance | spearman_ic_stance | hit_rate_stance | avg_monthly_strategy_return_stance | monthly_strategy_volatility_stance | information_ratio_annualized_stance | max_drawdown_stance | positive_signal_share_stance | avg_abs_score_stance | pearson_ic_delta | hit_rate_delta | information_ratio_annualized_delta | max_drawdown_delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| refined_subset | BOC | CAD | 74 | -0.0907 | -0.0126 | 0.5541 | 0.0013 | 0.0184 | 0.2379 | -0.1356 | 0.3919 | 0.4474 | existing_currency_stance | 75 | -0.1151 | 0.0264 | 0.4667 | 0.0004 | 0.0183 | 0.0727 | -0.1862 | 0.4400 | 0.9973 | 0.0244 | 0.0874 | 0.1651 | 0.0506 |
| refined_subset | BOE | GBP | 74 | -0.1311 | -0.0850 | 0.4595 | -0.0015 | 0.0228 | -0.2289 | -0.2955 | 0.4054 | 0.5886 | existing_currency_stance | 74 | 0.1500 | 0.1100 | 0.5541 | 0.0018 | 0.0227 | 0.2776 | -0.1669 | 0.4865 | 0.4888 | -0.2811 | -0.0946 | -0.5064 | -0.1286 |
| refined_subset | BOJ | JPY | 74 | -0.0190 | -0.0188 | 0.4595 | -0.0011 | 0.0287 | -0.1349 | -0.2568 | 0.6216 | 0.6406 | existing_currency_stance | 75 | -0.0542 | -0.1060 | 0.4533 | -0.0061 | 0.0279 | -0.7545 | -0.4952 | 0.6133 | 0.7269 | 0.0352 | 0.0061 | 0.6196 | 0.2384 |
| refined_subset | ECB | EUR | 74 | 0.0074 | 0.0220 | 0.6216 | 0.0030 | 0.0210 | 0.5002 | -0.1607 | 0.6081 | 0.4118 | existing_currency_stance | 75 | -0.0441 | -0.0626 | 0.5333 | 0.0008 | 0.0212 | 0.1333 | -0.1689 | 0.6267 | 0.5685 | 0.0515 | 0.0883 | 0.3669 | 0.0082 |
| refined_subset | FED | USD | 72 | 0.1797 | 0.1566 | 0.5417 | 0.0034 | 0.0202 | 0.5829 | -0.0977 | 0.4306 | 0.4909 | existing_currency_stance | 72 | 0.1976 | 0.1538 | 0.5417 | 0.0012 | 0.0204 | 0.2059 | -0.1072 | 0.4722 | 2.2285 | -0.0179 | 0.0000 | 0.3771 | 0.0095 |
| refined_subset | RBA | AUD | 74 | -0.0791 | -0.0459 | 0.5135 | -0.0004 | 0.0289 | -0.0432 | -0.2367 | 0.5405 | 0.5368 | existing_currency_stance | 75 | -0.1066 | -0.0259 | 0.5200 | 0.0027 | 0.0286 | 0.3285 | -0.1634 | 0.4400 | 0.5451 | 0.0276 | -0.0065 | -0.3717 | -0.0733 |
| refined_subset | RBNZ | NZD | 74 | -0.0267 | -0.0377 | 0.4865 | -0.0006 | 0.0304 | -0.0703 | -0.3106 | 0.5000 | 0.5349 | existing_currency_stance | 74 | -0.1547 | -0.1050 | 0.4324 | -0.0017 | 0.0300 | -0.1980 | -0.2588 | 0.4324 | 1.3063 | 0.1280 | 0.0541 | 0.1277 | -0.0517 |
| refined_subset | SNB | CHF | 74 | -0.0429 | -0.0651 | 0.4730 | -0.0014 | 0.0219 | -0.2203 | -0.1684 | 0.4189 | 0.5022 | existing_currency_stance | 74 | -0.0876 | -0.0612 | 0.5270 | -0.0002 | 0.0220 | -0.0267 | -0.1414 | 0.4459 | 1.0541 | 0.0446 | -0.0541 | -0.1937 | -0.0270 |

## Maintenance and Update Procedures

1. Refresh macro releases from EODHD.
2. Rebuild `processed.eda_observations` and EDA analysis outputs.
3. Rebuild currency strength weights and refinement outputs.
4. Run `python -m scripts.build_production_currency_strength`.
5. Review monitoring snapshot, validation metrics, and latest signal changes before publishing.
