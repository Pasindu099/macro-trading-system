# Step 16 follow-ups — 21 October 2026

## 1. BoC model input audit

| Input | Value | Source | As of |
| --- | ---: | --- | --- |
| Real neutral rate | 1.759161% | [NY Fed HLW Canada](https://www.newyorkfed.org/research/policy/rstar), 2026 Q2 vintage published 28 Aug | 30 Jun 2026 |
| Core inflation | 1.85% YoY | [BoC Valet CPI-trim 1.8%, CPI-median 1.9%](https://www.bankofcanada.ca/valet/observations/CPI_TRIM,CPI_MEDIAN/json); reference month, release timestamp unavailable | Sep 2026 |
| Inflation target | 2.0% midpoint of 1–3% band | `config/central_banks.yaml` | 21 Oct 2026 |
| Output gap | −0.268123% | [OECD Economic Outlook 119](https://www.oecd.org/en/topics/sub-issues/economic-outlook.html), 2026 estimate published 3 Jun | 3 Jun 2026 |
| 3-month market leg | 2.499% | EODHD `CA3M.GBOND`; **bill-based proxy**, low confidence | 20 Oct 2026 |

Calculation: 1.759161 + 1.85 + 0.5(1.85 − 2) + 0.5(−0.268123) = **3.4001%**; less 2.499% bill = **+90.11 bp**. Latest stored BoC policy decision rate is 2.25% (2 Sep), so the bill is 24.9 bp above spot policy; it is a yield proxy, not a policy-rate forecast. No wrong unit or headline/core swap found. Before/after model result: **3.4001% / +90.11 bp / low** unchanged; desk now says when the gap is against a bill-based proxy. See `app/web/templates/desk/panels/gap.html` and `fedview.html`.

Desk-page stall audit: isolated test and the original first-three sequence both passed with `faulthandler_timeout=60`; no hang traceback exists to assign a file:line. `pg_stat_activity` showed only the diagnostic query active and no lock wait; no app job appeared in the five-minute scheduler log window. The integration module already sets `ENABLE_SCHEDULER=false`. A 45-second test-level alarm now fails `/desks` promptly if it stalls again (`tests/integration/test_admin_endpoints.py`). Focused test passed; full container suite 827 passed, 7 skipped.
