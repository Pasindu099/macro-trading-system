# Overview v2 status

| Part | Status | Evidence |
| --- | --- | --- |
| 0 | Verified | News and regime-map fixes; NZ labor calendar-window and episode-date bugs corrected. |
| A | Backfilled through 4 Oct | Daily four-factor 20d/60d attribution, fixed tier rules and all-eight ranking header. |
| B | Complete in code | One card per situation, grouped currencies/pairs, severity, since date, evidence and first available link. |
| C | Complete in code | Max six recent investingLive items with currency and situation/tier chips; intraday reaction pending label. |

- `app/services/overview.py:79`: News reads gated investingLive items from the past 24 hours through now, max six.
- `app/services/overview.py:134`: Calendar ISO timestamp is parsed before the regime-map template formats it.
- `app/services/situations.py:75`: Historical release, yield, spot, and spread queries exclude future observations.
- `app/services/situation_rules.py:61`: Active episode evidence updates during exit hysteresis.
## Parts 0–C verification
- Compose Postgres healthy; migration 0029 applied. In-container full suite: 543 passed, 7 skipped. Earlier 6 failures/20 errors were host-side DB access; no reproduced pre-existing failures.
- Dominance backfill wrote 1,446 snapshots. Today's rerun wrote 0; newest complete snapshot is 4 Oct 2026, so today's shares are pending.
- Latest 20d shares (4 Oct): risk 41.4%, rates 49.4%, data 3.2%, oil 6.0%; dominant tier: CB policy divergence. Seven USD majors contributed.
- NZD labor: current gap 0.3000pp; exit requires <0.3pp for 5 days. The old episode ended 6 Nov 2025; current status is watch. Calendar-window and floating-point bugs fixed in `app/services/situations.py:148`.
- USD/JPY intervention: last spot 157.83 on 2 Oct; distance 0.1076% from zone versus exit >5% for 5 days. Current spot evidence is stale/pending; episode remains active. Thresholds unchanged.
- Date comparison for stored episodes fixed in `app/services/overview.py:128`; all seven live panel builders and templates render in-container. Focused tests: 12 passed.
