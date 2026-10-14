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

## Part B: projection loaders

| Bank | Source (deterministic) | Rounds loaded | Not loaded | Conditioning |
| --- | --- | --- | --- | --- |
| BOE | MPR HTML central-projection table + annex Q4 CPI | 17 (May 2022 → Jul 2026) | 2020–21, Feb 2022 PDF only; Apr 2026 scenarios only | market path (Bank Rate stored) |
| BOC | MPR `/projections/` HTML: annual + Q4/Q4 | 7 (Jul 2024 → Jul 2026) | pre-Jul 2024 PDF only; Apr/Jul 2025 scenario reports | own (not published) |
| RBNZ | MPS data workbook, `Projections` sheet | 24 (2020 → Sep 2026) | May/Aug 2020 (unconstrained OCR −2.3/−2.1 fails bounds), Feb 2021 (no OCR column) | own OCR track → `policy_rate` |
| SNB | data.snb.ch cube `snbiprogq` (CSV) | 27 (Mar 2020 → Sep 2026) | — | constant rate (stored as `stat=assumption`) |
| BOJ | — | 0 | Outlook Report PDF only (skipped, no LLM) | board median |
| RBA | — | 0 | rba.gov.au returns 403 to every scripted request (host and container) | market path |

- Parsers `app/processing/cb_projection_parsers.py`; stdlib xlsx reader `app/processing/xlsx_reader.py` (no openpyxl); loader/revisions `app/services/cb_projection_loaders.py`; `scripts/load_cb_projections.py`; `job:cb_projections` daily 07:30 UTC (last 120 days).
- Migration `0032`: `stat` also allows `point` and `assumption`. Horizons are `YYYY` or `YYYYQn`. Round date is the decision date in the publication month; first of month where no decision is stored (SNB 2020–22, RBNZ six files; listed in loader output).
- **RBNZ blocks the container (Cloudflare 403 on python TLS)**; workbooks were downloaded on the host with curl into `data/rbnz_mps/` (gitignored) and loaded with `--rbnz-dir`. The daily job will log the 403 for RBNZ unless run from a host that passes.
- Bounds: CPI −5…25, GDP −25…25, unemployment 0…30, policy rate −2…20. BoC rejects a page whose horizons do not match the round (old URLs redirect to Jul 2024).
- Latest revisions (Fed rule; flag needs a policy path): BOE Feb→Jul 2026 CPI 2026Q4 +1.2 → **unavailable** (no common Bank Rate horizon); BOC Apr→Jul CPI 2026Q4 +0.2 → unavailable (no path); RBNZ May→Sep CPI 2026Q4 −0.2, OCR −0.03 → **none**; SNB Jun→Sep CPI 2026Q4 +0.4 → unavailable (constant rate).
- API: `/api/cb/{BOE|BOC|RBNZ|SNB|BOJ|RBA}/projections` and `/revisions`. Tests: `test_cb_projection_parsers.py` (11, saved fixtures in `tests/fixtures/cb_projections/`). Full suite: **627 passed, 7 skipped**.

## Part C: statements and diff

- `app/processing/cb_statements.py` (no LLM): HTML via bs4 (BoJ 2026 PDFs via existing `pypdf`), stored in `cb_policy_documents` as `doc_type='policy_statement'` (the older PDF/LLM ingester rewrites `statement` rows unless analysed, so a separate type avoids two writers). Dates = stored decisions ∪ official config meetings (history has gaps, e.g. FED 29 Jul, BoJ 28 Apr 2026). The old `scrape_bank` returned 0 for 6 banks (site changes) and wrong dates for BoC, so it is not used.
- 13-month backfill (`scripts/load_cb_statements.py`): FED 9, ECB 8 (press-conference statement, Q&A cut), BOE 9 (Monetary Policy Summary only), BOJ 9 (4 HTML + 5 PDF), BOC 9, RBNZ 7 (host-downloaded, `--local-dir`; Cloudflare), SNB 5. **RBA 0** (403). Re-run: unchanged rows are not rewritten.
- Schedule: one run 30 min and one 3 h after each decision in `cb_meetings.yaml` for the 7 reachable banks (60-day window). RBNZ runs will log the Cloudflare 403 in-container.
- RBNZ 28 Oct and 9 Dec 2026 confirmed on RBNZ event pages → `official: true` (synced).
- `app/services/cb_statement_diff.py` + `GET /api/cb/{bank}/statement-diff`: line-aware sentence split, dates masked in the comparison key, difflib opcodes → changed (old/new, similarity ≥ 0.4) / added / removed; topic by word-start keywords. No hawkish/dovish tags.

| Bank | Previous → latest | Changed sentences | Largest topics |
| --- | --- | ---: | --- |
| FED | 29 Jul → 16 Sep 2026 | 11 | growth 4, policy 3 (vote 9–3 → 12–0; maintain → raise) |
| ECB | 23 Jul → 10 Sep | 102 | inflation 35, growth 28 |
| BOE | 30 Jul → 17 Sep | 18 | inflation 8, policy 4 |
| BOJ | 31 Jul → 18 Sep | 91 | growth 21, inflation 18 (PDF text; 35 "other") |
| BOC | 15 Jul → 2 Sep | 79 | growth 28, inflation 19 |
| RBNZ | 8 Jul → 2 Sep | 218 | inflation 78 (release includes the record of meeting) |
| SNB | 18 Jun → 24 Sep | 68 | inflation 31, growth 22 |

- Weak spots: long statements (ECB, RBNZ) rewrite most sentences, so counts are high; PDF extraction adds spacing artefacts (BoJ "202 6") and table/footnote lines classed "other".
- Tests: `test_cb_statements.py` (7, FED fixtures in `tests/fixtures/cb_statements/`). Full suite: **634 passed, 7 skipped**.
