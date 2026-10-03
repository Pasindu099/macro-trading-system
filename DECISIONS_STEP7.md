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
