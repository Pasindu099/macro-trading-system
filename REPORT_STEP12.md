# Step 12 — Central Bank Terminal

## Part A: config, blackout, summary

- `config/central_banks.yaml`: all 8 banks `verify: false`. Checked on official pages: BoE/BoJ/RBA/SNB/Fed/ECB meeting calendars, BoC 2027 schedule release, RBNZ OCR timeline. Corrections: BoE MPR months now 2/4/7/11; BoC Governing Council 7 members; RBNZ MPC 6 (3+3), 8 reviews/yr, MPS months 2/5/9/12; SNB summary of discussions after 28 days (not 14).
- `config/cb_meetings.yaml` dates were wrong for BoE (Jul 30 not Aug 6), RBA (Sep 29, no Jul meeting), BoC (Jul 15, Sep 2), SNB (Dec 10 not 17), BoJ/RBA/BoC/RBNZ 2027 and Fed Jun 2027 (8–9 Jun). `scripts/sync_cb_meetings.py` brought the DB in line: 36 deleted, 46 inserted, 19 flags updated; rerun 0.
- Unverifiable: RBNZ Oct 28/Dec 9 2026 and May–Sep 2027 (official site 403/JS; secondary calendars → `official: false`); BoC Mar/Jun 2026 (omitted, past); BoJ projection rate assumption; RBNZ mandate wording (remit page not reachable).
- Blackout rules (`blackout:` per bank): Fed 2nd Saturday before → day after; ECB 7 days; BoE 8 days (code says 8–9, `approx`); BoC 7 days / 8 in MPR months; RBA Wednesday before. **Unverified:** BoJ (2 business days, 2001 notice only; JP holidays ignored), RBNZ and SNB (no published rule → status `unknown`).
- `app/services/cb_summary.py` + `GET /api/cb/{bank}/summary` (all 8). Live, 14 Oct 2026:

| Bank | Rate | Last move | Regime | Next | Blackout from | Priced next | 2Y−policy | Gap |
| --- | ---: | --- | --- | --- | --- | --- | ---: | --- |
| FED | 4.00 | +25 16 Sep | hiking | 28 Oct | 17 Oct | +5.5bp (futures) | +80bp | bp |
| ECB | 2.50 | +25 10 Sep | hiking | 29 Oct | 22 Oct | +5.2bp approx. | +61bp | unavailable (no MPD loaded locally) |
| BOE | 3.75 | hold 17 Sep | holding | 5 Nov | 28 Oct | +13.1bp approx. | +97bp | pending |
| BOJ | 1.25 | +25 18 Sep | hiking | 30 Oct | 27 Oct (unverified) | +13.3bp approx. | +66bp | pending |
| RBA | 4.60 | +25 29 Sep | hiking | 3 Nov | 28 Oct | hidden (100bp) | +32bp | pending |
| BOC | 2.25 | hold 2 Sep | holding | 28 Oct | 20 Oct | +3.8bp approx. | +94bp | pending |
| RBNZ | 2.75 | +25 2 Sep | hiking | 28 Oct (unofficial) | unknown | hidden (stale 9 Oct) | +108bp | pending |
| SNB | 0.00 cfg | — | unavailable | 10 Dec | unknown | +0.6bp approx. | +24bp | pending |

- SNB: EODHD publishes every decision since Jun 2025 with a null actual (0% omitted), so the rate is the config fallback and regime is unavailable rather than inferred.
- Tests: `test_cb_summary.py` (7), `test_central_banks_config.py` (4). Full suite: **616 passed, 7 skipped**.
