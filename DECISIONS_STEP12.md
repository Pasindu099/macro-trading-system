DECISIONS — STEP 12: CENTRAL BANK TERMINAL

Spec: design/design-mockups/CentralBanks.dc.html (Overview + Projections views). Rules: AGENTS.md; in-container; one part per commit; focused tests; full suite once per part; long jobs detached. No LLM anywhere in this step. Sessions: stop after Part D and after Part F with a ≤10-line status.

Scope decided:

Phase 1 (Parts A–D): all 8 banks — summary, projections, statements, calendar.
Phase 2-lite (Parts E–F): speech pipeline with WORD-LIST tone scoring. An AI scoring layer is added later; store the method version with every score.
Parts G–H: page + desk integration.
Phase 3 (later step, not now): speech search with a LOCAL embedding model + AI assistant.

New dependencies: avoid. If an HTML/XML parser is needed and none is installed, report the proposed package and stop for approval.

PART A — BANK CONFIG + SUMMARY (all 8)
config/central_banks.yaml: verify the six verify: true banks against official sources (mandate, target, meeting schedule 2026–2027, voting structure, projection publication, rate-path type, conditioning). Set verify: false when checked; list anything unverifiable.
Blackout rules per bank in config (e.g. Fed: second Saturday before the meeting through the day after; ECB: 7 days before). Mark unverified rules.
Summary service per bank: policy rate + last decision (from rate-decision history), regime (existing model), next meeting, blackout status, priced next move (Fed futures; others existing proxy, "approx.", hidden if |move| > 50bp or stale > 3 days), 2Y − policy, gap (Fed bp / ECB qualitative / others "pending").

PART B — PROJECTION LOADERS (BoE, BoJ, RBA, RBNZ, BoC, SNB)
Deterministic parsing only (official HTML tables, CSV/Excel downloads). Sources: BoE Monetary Policy Report, BoJ Outlook Report, RBA Statement on Monetary Policy, RBNZ Monetary Policy Statement, BoC Monetary Policy Report, SNB conditional inflation forecast.
Store in cb_projection_values; allow horizons as years or quarters (e.g. 2026, 2026Q4). RBNZ's own OCR track → variable = policy_rate.
Rounds from 2020 where available. Validate plausible bounds per variable.
Record conditioning per bank (market path / constant rate / own track …).
If a bank's projections exist only in PDF: report it, skip it, no LLM.
Revisions vs previous round + reaction-function flag (same rules as Fed).

PART C — STATEMENTS (all 8)
Fetch each bank's policy decision statements for the last 13 months into cb_policy_documents (reuse the existing ingester pattern); schedule on decision days from config.
Statement diff service (difflib, sentence level): latest vs previous; changed sentences only, old vs new; topic per sentence by keyword (policy stance / inflation / labor / growth / balance sheet / FX).
No hawkish/dovish tagging of statements in this step.

PART D — UPCOMING
Per bank: next meetings, blackout windows (from Part A rules), minutes / projection release dates where known.
Scheduled speeches only where a structured source exists (e.g. Fed and ECB calendars/feeds); other banks "speech calendar pending". STOP — ≤10-line status.

PART E — SPEECH PIPELINE (no AI)
config/cb_officials.yaml: per bank, every policymaker: name, aliases, role, voter status by year (Fed rotation), start/end dates.
Ingest speeches from official RSS/HTML per bank (Fed board + regional Feds where feeds exist, ECB, BoE, BoJ, RBA, RBNZ, BoC, SNB) into cb_speeches(bank, speaker_id, date, title, url, text, source); dedupe; backfill 12 months; daily job.
Short comments: attribute investingLive central-bank headlines to officials by name/alias matching → cb_speaker_comments.
Point extraction (rule-based): split into sentences; keep sentences with policy/economy keywords; theme per sentence by keyword dictionary (inflation / labor / growth / financial conditions / FX / other).

PART F — WORD-LIST TONE SCORING (method "lexicon_v1")
config/tone_lexicon.yaml: hawkish and dovish phrases with weights, plus negation and hedging handling (e.g. "not yet confident", "premature to cut", "further tightening may be appropriate", "risks have become more balanced"). Sentence score −2…+2; clip.
Store scores with method = lexicon_v1 so a later AI method can sit alongside without overwriting.
Per speaker: 30-day tone, own 6-month baseline, shift (|shift| ≥ 0.4 = "shifting"), lean (≥ 0.5 hawkish, ≤ −0.5 dovish), focus mix (theme shares).
Committee balance per bank: weekly hawkish / neutral / dovish shares over 12 weeks (speakers active that week; voters marked).
Quality check in the report: 30 random scored sentences with their score, for manual review; known weak spots listed. STOP — ≤10-line status incl. speeches ingested per bank.

PART G — CB TERMINAL PAGE (/central-banks)
Bank tabs (8); views Overview / Projections, per the mockup.
Overview: summary (A), committee balance (F), statement tracker (C), speaker tracker + profile (E/F, labelled "word-list tone, v1"), upcoming (D), latest CB headlines per bank.
Projections: revisions table (median / central tendency / range where published), dots vs market (Fed only), risk balance (Fed), CB Tracking, vintages, format table; six banks from Part B.
Speech library search + "Ask" drawer: show as "Phase 3 — pending".
States: loading / empty / unavailable / pending / error; 60 s cache; responsive to 390 px; shared nav + freshness indicator.
Colours: amber/blue = hawkish/dovish; green/red only for price moves.

PART H — DESK INTEGRATION
USD and EUR desks: Fed/ECB view uses the statement tracker (C) and committee balance (F); "speaker focus mix" bar from F (labelled v1).
Overview: CB regime map guidance column may use statement-diff presence only (no tone) — otherwise unchanged.

TESTS
Config validation; blackout windows; each projection parser on saved fixtures; statement diff on fixture statements; official name/alias matching; lexicon scoring incl. negation cases; baseline/shift maths; committee balance shares; page panels (data / pending states). Full suite green in-container.

DELIVER
REPORT_STEP12.md (≤60 lines) + ≤10-line status after each STOP and at the end.

DECISION — blocked / PDF-only sources
1. RBA and RBNZ: try ONE request each with an honest identifying
   User-Agent ("ForexCompass/1.0 (research dashboard; <contact email from
   settings>)"), respecting robots.txt, ≥ 10 s between requests. No
   browser spoofing, no Cloudflare workarounds. Report the result.
2. Where still blocked (and for BoJ projections, PDF-only): add an admin
   page /admin/cb-imports to (a) upload the official file (RBNZ/RBA data
   files, statement HTML/PDF text) and run the existing parser on it, and
   (b) manually enter projection values (bank, round date, variable,
   horizon, value) with the same validation rules as the loaders.
3. Jobs for blocked sources log status "manual_import" (not error) and the
   freshness indicator shows "manual import — last updated {date}".
4. Speech pipeline (Part E): same rule — skip blocked sites gracefully,
   rely on investingLive comments for those banks, report coverage per bank.

Then continue with Parts E and F; stop after F with a ≤10-line status
including speeches ingested per bank and the 30-sentence quality sample
location.
