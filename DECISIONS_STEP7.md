Read AGENTS.md, PROGRESS.md, REPORT_STEP6.md first. Save this prompt
verbatim to DECISIONS_STEP7.md. Add sub-tasks to PROGRESS.md; commit per part.

PART A — PIPELINE HEALTH (report only, ≤20 lines in REPORT_STEP7.md)
- For each pipeline, report last successful run and newest row:
  CB policy documents (scraper + ingester + analyzer), news monitor
  (news_alerts), news_pipeline (raw_news, enriched_news), CB feed poll,
  rateprobability scraper, OIS fetch.
- For each one that is stale or empty: is it scheduled? disabled by
  config? failing (last error)? never run?
- Give me read-only SQL I can run on production to compare (row counts and
  newest timestamps for the same tables). Do not fix anything in this part.

PART B — MISSING INDICATORS
- US real GDP: find why no indicator exists. Check the EODHD calendar
  event names for GDP (advance/second/third estimate, QoQ annualised) and
  the canonicalizer mapping in config/indicator_mapping.yaml. Add the
  mapping, re-run ingestion for the affected window, confirm rows exist.
- ISM manufacturing production, ISM services prices paid, ISM services new
  orders: same check. If EODHD doesn't publish them, say so; don't invent
  a substitute.
- After mapping: the USD desk Key data GDP card and ISM series render live.
- Run scripts/check_desk_indicators.py and report the result.

PART C — RESTART STALE PIPELINES
- Fix whatever Part A found for news_alerts and CB policy documents,
  within the existing design (schedule, config, credentials, or a bug).
  If a fix needs a decision (e.g. an API key, a paid source), ask ONE
  question and stop.
- CB policy documents: run the existing scraper/ingester for the Fed's
  latest statements and minutes (last 13 months) so the Fed view panel
  has data. Use the cheapest configured model for the analyzer.

PART D — RATE PROBABILITY METHODOLOGY (audit items #6–#9)
- ZQ: treat each contract as the monthly AVERAGE of EFFR. Derive the
  implied post-meeting rate for a meeting month by de-averaging:
  r_post = (avg_month × days_in_month − r_pre × days_before_meeting)
           / days_after_meeting. Use the next month's contract when the
  meeting falls in the last 7 days of a month.
- Step / piecewise-constant path between meetings instead of linear
  interpolation.
- Static overrides: never serve an override older than 14 days; mark the
  view "stale source" instead.
- Fix tests/unit/test_rate_probability.py:182 to compute the next meeting
  from config instead of a hard-coded date. The suite should then pass
  without deselecting anything.
- Report: next 3 Fed meetings, probabilities before vs after the fix.

PART E — PRODUCTION READINESS OF FRONTEND ASSETS
- Vendor HTMX and ECharts into app/web/static/vendor/ at pinned versions;
  remove runtime CDN loads (Google Fonts may stay).

TESTS
- Indicator mapping tests for the new GDP/ISM names.
- De-averaging: known example (meeting mid-month) gives the correct
  post-meeting rate; end-of-month meeting uses the next contract.
- Piecewise-constant path between meetings.
- Override older than 14 days is not served.
- Full suite once per part, with NO deselected tests at the end.

DO NOT: new desks or pages; change Event Innovation, Macro State, rates
or positioning logic; add Python dependencies.
DELIVER: REPORT_STEP7.md (≤60 lines) incl. the prod SQL, pipeline
verdicts, before/after Fed probabilities, and test results.

DECISION — Step 7 Part C (append to DECISIONS_STEP7.md)

1. OPENAI: I will top up credits and set a hard monthly limit myself.
   - Switch ALL OpenAI usage to gpt-4o-mini via one setting
     (OPENAI_MODEL); remove per-module model overrides like gpt-5.1.
     Resolve the settings.py vs docker-compose model conflict from the audit.
   - CB documents: run the scraper/ingester/analyzer for the Fed (last 13
     months: statements + minutes) once credits are available. If the call
     fails for credits, report and continue with the rest.
   - Schedule the CB document pipeline: run on FOMC/ECB decision days
     (statement, +30 min after release) and minutes release days, from the
     meeting config. Not on a fixed timer.

2. NEWS AI: DISABLE LLM enrichment for news_alerts and enriched_news
   (config flag, default off). Keep raw_news collection running. Mark the
   desk News panel "AI notes paused" rather than erroring. A new tiered
   design comes in step 12. Do not delete the enrichment code.

3. RATEPROBABILITY.COM: disable the scraper job (403 since Aug). Do not try
   to bypass the block. The own engine (Part D) is the primary source;
   pages show rateprobability data only if fresh (< 3 days), else hide it.

4. OIS FETCH BUG: fix the doubled proxy URL prefix for BOC, BOJ, RBA, RBNZ,
   SNB. Change the job so a fallback to cached data older than 3 days logs
   status "stale" (not "ok") and shows in /api/admin/jobs/status.
   Re-run and report which banks now return fresh data.

5. NEW PART F — EODHD 1,000-EVENT CAP (after Part E):
   - Confirm whether the calendar endpoint caps at 1,000 events per request.
   - Re-fetch the full calendar history in chunks small enough to stay under
     the cap (e.g. monthly, or per country per quarter), dry-run first,
     reporting events found vs already stored per chunk.
   - Ingest only missing events, using the fixed reclassify logic (newest
     payload wins). Report how many events were missing and for which
     indicators.

Then continue Parts D, E and F. Update PROGRESS.md, commit per part.
