# Step 12 — Central Bank Terminal

## A: config, blackout, summary
- `central_banks.yaml`: all 8 `verify: false` (official calendars, BoC 2027 release, RBNZ timeline/event pages). Fixed: BoE MPR months 2/4/7/11, BoC council 7, RBNZ MPC 6 / 8 per year, SNB summary at 28 days. Meeting dates were wrong for BoE, RBA, BoC, SNB, Fed Jun 2027 → `scripts/sync_cb_meetings.py` (36 deleted, 46 inserted, 21 flags).
- Unverifiable: RBNZ May–Sep 2027 (secondary, `official: false`), BoC Mar/Jun 2026 (omitted), BoJ rate assumption, RBNZ remit wording.
- Blackout: Fed, ECB, BoE (8 days, approx), BoC (7/8 in MPR months), RBA verified; **BoJ unverified** (2001 notice), **RBNZ/SNB none published** → `unknown`.
- `cb_summary.py`, `/api/cb/{bank}/summary`. 14 Oct: FED 4.00 hiking, priced +5.3bp (futures), 2Y−policy +80bp, gap bp; ECB 2.50 hiking, +5.2bp approx.; BOE 3.75 holding +13.1; BOJ 1.25 hiking −36.7 approx.; RBA 4.60 +0.2; BOC 2.25 holding +3.8; RBNZ 2.75 hidden (curve stale 9 Oct); SNB 0.00 (config; EODHD omits 0% actuals) +0.4. Non-Fed gaps "pending".
- Fix (Part D): the probability module measures non-FED/ECB moves from stale `current_rate` config (BoJ 0.75, RBA 3.60, RBNZ 2.25); the summary now uses implied − stored decision rate (RBA was shown as "100bp hidden").

## B: projections (`cb_projection_parsers.py`, `cb_projection_loaders.py`, migration 0032 `point`/`assumption`)
| Bank | Source | Rounds | Not loaded |
| --- | --- | --- | --- |
| BOE | MPR HTML central table + annex Q4 CPI | 17 (May 2022–Jul 2026) | 2020–Feb 2022 PDF; Apr 2026 scenarios only |
| BOC | MPR `/projections/` HTML annual + Q4 | 7 (Jul 2024–Jul 2026) | earlier PDF; Apr/Jul 2025 scenario reports |
| RBNZ | MPS data xlsx (stdlib reader), OCR → `policy_rate` | 24 (2020–Sep 2026) | May/Aug 2020 unconstrained OCR < −2; Feb 2021 no OCR |
| SNB | data.snb.ch `snbiprogq` CSV, constant-rate assumption | 27 (2020–Sep 2026) | — |
| BOJ / RBA | — | 0 | BoJ PDF only (skipped); RBA 403 to all scripted requests |
- RBNZ is behind Cloudflare for the container: workbooks fetched on host into `data/rbnz_mps/` (gitignored), `--rbnz-dir`. Daily `job:cb_projections` 07:30 UTC (120 days) will log the RBNZ 403.
- Revisions (Fed rule): RBNZ May→Sep CPI 2026Q4 −0.2, OCR −0.03 → none; BOE CPI +1.2 → unavailable (no common Bank Rate horizon Feb→Jul); BOC/SNB unavailable (no policy path).

## C: statements (`cb_statements.py`, `cb_statement_diff.py`, `/statement-diff`)
- `doc_type='policy_statement'` (the old LLM ingester rewrites `statement`). 13 months: FED 9, ECB 8 (Q&A cut), BOE 9, BOJ 9 (5 PDF), BOC 9, RBNZ 7 (host files), SNB 5, **RBA 0**. Runs 30 min and 3 h after each decision. Old `scrape_bank` returned 0 for 6 banks.
- Latest diff (changed sentences): FED 11 (vote 9–3 → 12–0; maintain → raise), BOE 18, SNB 68, BOC 79, BOJ 91, ECB 102, RBNZ 218 (includes record of meeting). Dates masked; topics by word-start keywords; PDF text adds "other" noise.

## D: upcoming (`cb_upcoming.py`, `/api/cb/{bank}/upcoming`)
| Bank | Next | Blackout from | Projections | Minutes-type release | Speeches |
| --- | --- | --- | --- | --- | --- |
| FED | 28 Oct | 17 Oct | Dec SEP | 18 Nov (exact) | 3 (Board, calendar.json) |
| ECB | 29 Oct | 22 Oct | Dec | ~26 Nov | 16 (weekly schedule) |
| BOE | 5 Nov | 28 Oct (approx) | Nov MPR | same day | pending |
| BOJ | 30 Oct | 27 Oct (unverified) | Oct Outlook | SoO 10 Nov (official) | pending |
| RBA | 3 Nov | 28 Oct | Nov SMP | ~17 Nov | pending |
| BOC | 28 Oct | 20 Oct | Oct MPR | ~10 Nov | pending |
| RBNZ | 28 Oct | unknown | Dec MPS | same day | pending |
| SNB | 10 Dec | unknown | every meeting | ~7 Jan | pending |
- Fed calendar covers Board members only (no regional presidents). Speech calendars cached 1 h.

## Decision: blocked / PDF-only sources
- Honest UA `ForexCompass/1.0 (research dashboard)` (no `CONTACT_EMAIL` set; new setting), robots.txt checked, 10 s spacing: **RBA** first request 200, next three 403 → blocked; **RBNZ** robots.txt 403 → no page request. All fetchers now use this UA (browser-spoof UA removed; FED/ECB/BoE/BoJ/BoC/SNB still 200). Host-curl and RBNZ sitemap paths removed.
- `/admin/cb-imports` (admin): upload RBNZ xlsx / BoE·BoC HTML projections or any statement (HTML/PDF/TXT) through the existing parsers; manual projection entry with loader bounds and `YYYY`/`YYYYQn` horizons. Audit table `cb_manual_imports` (migration 0033).
- Manual sources in `central_banks.yaml` (`manual_import`): BoJ projections; RBA and RBNZ projections/statements/speeches. Their jobs log status `manual_import` (`job:cb_*:{BANK}`); freshness rows read "manual import — last updated {date}".
- **Open:** RBNZ's 24 projection rounds and 7 statements stored earlier came from host curl with a browser UA (now disallowed).

## Tests
- New: summary 7, config 4, projection parsers 11, statements 7, upcoming 4. Full suite after D: **638 passed, 7 skipped**.
