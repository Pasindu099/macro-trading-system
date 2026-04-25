# Monitoring and Maintenance Plan

## KPIs

- Indicator coverage by currency.
- Latest source release age.
- Monthly Pearson/Spearman information coefficient versus forward FX returns.
- Directional hit rate.
- Strategy information ratio and max drawdown.
- Signal turnover and large one-period score changes.
- Data quality flags from the EDA layer.

## Reporting Schedule

- Daily: data freshness and latest signal availability.
- Weekly: indicator coverage and large signal changes.
- Monthly: validation metrics after FX returns are available.
- Quarterly: weight recalibration review and stakeholder sign-off.

## Feedback Loop

Collect trader/researcher annotations for false positives, regime shifts, and indicators that behaved counterintuitively. Feed those notes into the next weight refinement review.

## Update Process

1. Refresh macro data.
2. Rebuild EDA and currency strength weights.
3. Compare new weights against the previous production version.
4. Run backtest and monitoring checks.
5. Promote only after review if validation does not degrade materially.
