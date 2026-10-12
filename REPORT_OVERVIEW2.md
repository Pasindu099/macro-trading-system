# Overview v2 status

| Part | Status | Evidence |
| --- | --- | --- |
| 0 | Verified | News and regime-map fixes; NZ labor calendar-window and episode-date bugs corrected. |
| A | Backfilled through 4 Oct | Daily four-factor 20d/60d attribution, fixed tier rules and all-eight ranking header. |
| B | Complete in code | One card per situation, grouped currencies/pairs, severity, since date, evidence and first available link. |
| C | Complete in code | Max six recent investingLive items with currency and situation/tier chips; intraday reaction pending label. |
| D | Complete in code | Verdict-score ranking, six partial verdicts, all-eight rate-history regimes, positioning labels and score bars. |
| E | Complete in code | Eight-bank regime ladder, guidance, next meetings and freshness-gated priced moves. |
| F | Overall lens complete; policy values pending | 8×8 upper triangle, five groups, top-five concentration and widest-three list. |
| G | Complete in code | Fourteen-day high-importance releases grouped by country/time; eight rows with mandate/tracking context. |

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
- Part D: `app/services/overview.py:280`, `app/services/verdict.py:100`, `app/web/templates/overview/panel.html:12`. Live ranking 8/8 sorted, all 8 regimes present. Focused tests 15 passed; in-container full suite 545 passed, 7 skipped.
- Part E: `app/services/overview.py:366`, `app/web/templates/overview/panel.html:14`. Eight live rows rendered; Fed futures and five fresh approximate proxies shown, stale ECB/RBNZ `n/a`. Focused tests 12 passed; full suite 546 passed, 7 skipped.
- Part F: `app/services/overview.py:34`, `app/api/routes/shell.py:38`, `app/web/templates/overview/panel.html:16`. Overall lens 28/28; live top five share CHF. Policy path sublenses label cells pending because comparable 12-month ECB rate-path data is absent. Focused tests 14 passed; full suite 548 passed, 7 skipped.
- Part G: `app/services/overview.py:460`, `app/web/templates/overview/panel.html:18`. Live 8/8 rows; US CPI combines three components. Focused tests 15 passed; full suite 549 passed, 7 skipped.
