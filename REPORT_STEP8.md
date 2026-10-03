# Step 8 — Fed projections, tracking, regime and gap

## Part 0: Step 7 follow-ups

- Secrets: `app/log_redaction.py` strips `api_token`/`api_key`/`token`/`key` query params, `Authorization`/`X-Api-Key` headers, bearer tokens, `sk-` keys and Telegram `/bot<token>` paths. It is applied by the root `RedactingFormatter` (covers every HTTP client, tracebacks included), by EODHD error messages, and by `run_logger`'s stored errors. news_pipeline and the Telegram bot use a copy. Test: a logged httpx 422 for an EODHD URL contains no key.
- `.backfill_progress.json` is untracked; it, `data/*_checkpoint.json` and calendar-gap reports are now in `.gitignore`.
- Fed current rate: `resolve_current_rate` takes the latest released `fed_interest_rate_decision` (**4.00%, 16 Sep 2026**). `cb_meetings.yaml` is a fallback only (3.75%, `current_rate_as_of` 2026-05-10) and is labelled "Config fallback · as of …" on the desk.
- Event Innovation rebuild: the key diff found 0 changed identities (24,557 prints). Real run: **25,341 rows written** (7,141 score inserts, 17,416 updates, 411 orphans deleted; bundles +94 / 690 updated / 3 deleted). Macro State chain, all success: processed_dataset 31,998, feature_layer 26,174, cb_preferred_score 12,613, macro_indices 8,302, currency_stance 21,996.
- Top 40 unmapped events (`scripts/sql/top_unmapped_events.sql`; country/type, count): US API crude 368, US 4-week bill auction 365, Baker Hughes rigs 365, US 3/8/6-month bill auctions 363–364, EIA crude/gasoline/natgas/distillate/refinery stocks 357–361, MBA 30Y rate and applications 357–358, FR 3/6/12-month BTF auctions 357, ECB Lagarde speeches 307 (+161 alternate name), MBA refinance/market/purchase 263, JP 3-month bill 218, ECB Lane 217, US 17-week bill 207, ECB Guindos 196, US 30Y/15Y mortgage rate 186/184, Fed Williams 185, ECB Schnabel 175, Fed Bostic 171, CFTC silver/gold/aluminium/copper 163 each, Bundesbank Nagel 161. No mapping changes.

## Part A: per-CB config

- `config/central_banks.yaml`: mandate, inflation target, projection publication and cadence, rate path type, conditioning, voting, meetings and minutes for all 8 banks. FED is filled from primary sources (`verify: false`); ECB, BOE, BOJ, RBA, BOC, RBNZ and SNB are from public knowledge, `verify: true`. Test: `test_central_banks_config.py` (2).

## Part B: Fed projections (deterministic, no LLM)

- Migration `0024_cb_projections`: `cb_projection_values`, `cb_dots`, `cb_risk_balance`, `cb_projection_errors`. Parser `app/processing/fed_sep.py` (stdlib HTML parser; tables found by content); loader `app/services/fed_projections.py` (`job:fed_sep`, one transaction per round).
- **26 rounds loaded, 2020-06-10 → 2026-09-16, 0 rejected.** Rows: 2,795 values, 683 dot cells, 192 risk-balance rows, Table 2 RMSEs for 2020–2026. Skipped (PDF only): uncertainty/risk and Table 2 for June and September 2020. March 2022 is published as `fomcprojtable20220316.htm`, which is handled.
- Validation: median and central tendency within range; plausible bounds; dots per year = participants minus non-submitters declared for that meeting. The Fed's own notes cover June 2026 (one participant, no 2028) and September 2026 (one participant, no 2028/2029). Before the parser read those notes, both rounds were correctly rejected.
- Verified: March 2026 2026 medians GDP 2.4, UR 4.4, PCE 2.7, core PCE 2.7, funds 3.4; December 2025 PCE 2026 = 2.4. Latest (September 2026) 2026 medians: GDP 2.3, UR 4.1, PCE 3.7, core 3.4, funds 4.1.
- Tests: `test_fed_sep.py` (11) on the saved March 2026 fixture: values, dots, risk, errors, corrupted-round rejection, range parsing, scoped non-submitter notes, round discovery.
