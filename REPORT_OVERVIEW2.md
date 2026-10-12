# Overview v2 status

| Part | Status | Evidence |
| --- | --- | --- |
| 0 | Partial | News source/time guard and regime-map timestamp fixed; situation evidence refresh and historical-date guards added. |
| A | Pending | Dominance attribution and header not implemented. |
| B | Pending | Situation cards not grouped. |
| C | Pending | News chips/reaction layout not implemented. |

- `app/services/overview.py:79`: News reads gated investingLive items from the past 24 hours through now, max six.
- `app/services/overview.py:134`: Calendar ISO timestamp is parsed before the regime-map template formats it.
- `app/services/situations.py:75`: Historical release, yield, spot, and spread queries exclude future observations.
- `app/services/situation_rules.py:61`: Active episode evidence updates during exit hysteresis.
- NZD labor exit: unemployment gap below 0.3 percentage points for five days. USD/JPY intervention exit: distance above 5% from the 158–160.5 zone for five days. Current values unavailable because the configured database host did not resolve, including on approved retry. Thresholds unchanged.
- Focused tests: 16 passed. Full suite: 511 passed, 7 skipped, 6 failed, 20 errors; integration setup requires the unavailable database. No full-suite rerun.
