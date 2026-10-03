Continue the ForexCompass build. You are taking over from another agent. 
 
Read AGENTS.md, PROGRESS.md, REPORT_STEP8.md, REPORT_NULL_PERIODS.md first. 
Save this prompt verbatim to DECISIONS_STEP9.md. Commit per part. 
 
PART 0 — NULL-PERIOD INGESTION FIX (root cause) 
- In _upsert_release: when period_start_date AND period are both null, 
  identify a print by (indicator_id, released_at::date). A different 
  release date = a NEW print, never a revision of the previous one. Same 
  date with changed values = revision (as today). 
- Data repair script with --dry-run: for the 23 indicators, recompute 
  is_latest per (indicator_id, released_at::date). Report rows changed per 
  indicator, then run it for real. 
- Remove the Step 8 panel workaround so the curve panel reads is_latest 
  normally. Re-run Event Innovation incremental for affected indicators. 
- Tests: two decisions on different dates are both latest for their date; 
  a same-day correction is a revision. 
 
PART A — EUR DESK CONFIG 
- Enable EUR in config/desks.yaml: CB = ECB (config/central_banks.yaml), 
  yield benchmark DE, members: EZ, DE, FR (country monitor). 
- Key data, mapped to canonical names for EZ, DE and FR: HICP YoY, core 
  HICP YoY, unemployment, real GDP QoQ (NOT annualised; label it), HCOB 
  manufacturing + services PMI (sub-indices only if they exist), business 
  sentiment (Ifo DE, INSEE FR, EC economic sentiment EZ), wage growth. 
- Run scripts/check_desk_indicators.py for EUR; unmapped → unavailable, 
  never substituted. List gaps in the report. 
- Verify the ECB rows in central_banks.yaml against ECB sources; set 
  verify: false once checked. 
 
PART B — COUNTRY MONITOR SERVICE (EZ / DE / FR) 
- Latest values per indicator for the three, with colour logic relative to 
  EZ (stronger/hotter vs weaker/cooler; unemployment inverted). 
- Budget deficit % of GDP: Eurostat API (annual), stored in a small table. 
- OAT–Bund 10Y spread from the existing FR−DE spread. 
- Generic: the desk renders the country monitor only when the desk config 
  has members, so other desks are unaffected. 
 
PART C — ECB PROJECTIONS + TRACKING (deterministic, NO LLM) 
- Find a structured source for ECB/Eurosystem staff macroeconomic 
  projections (ECB Data Portal / SDMX, or the published tables). Load 
  HICP, core HICP, GDP, unemployment for every round from 2020. Same 
  tables as the Fed (cb_projection_values), stats = point projection 
  (no dots, no ranges unless published). Validate plausible bounds. 
- Tracking: HICP index levels from a structured source (ECB Data Portal or 
  Eurostat) for required-pace vs 3m annualised, same thresholds as the Fed. 
- Regime model: same rules on the ECB deposit facility rate history. 
- Gap for a market-conditioned bank (no rate path): compare the HICP 
  projection at the end of the horizon with the 2% target. Below target by 
  > 0.1pp → "dovish: projections imply more easing than priced"; above → 
  "hawkish"; else neutral. Return direction + pp, NO bp number. Label the 
  method in the API. 
 
PART D — ECB RATE PROBABILITIES 
- Apply proper maths to the ECB €STR source: step path between meetings, 
  meeting-date de-averaging if the instrument averages, 14-day override 
  rule. Report the next 3 ECB meetings before vs after. 
 
PART E — EUR PRICE DATA 
- EUR nominal effective exchange rate (EER-18 or EER-41, daily) from the 
  ECB Data Portal; store as a series. If unavailable, use EUR/USD and say so. 
 
PART F — DESK 
- /desks/EUR enabled, using design/design-mockups/EUR.dc.html as the spec: 
  price (EER + EUR/USD), country monitor, key data with the EZ/DE/FR 
  compare/country switch, ECB view (projections, revisions, statement 
  docs), ECB path, priced (€STR), 2Y Schatz vs DFR, curve (DE) + OAT–Bund, 
  gap (qualitative), positioning, catalysts (EZ/DE/FR), news. 
  Situations (French fiscal stress), verdict and scenarios stay 
  "after step 10". Governing Council balance stays unavailable. 
- Macro State: report whether the EUR composite uses single-mandate 
  weights. Report only, no change. 
 
TESTS: Part 0 tests; country colour logic; ECB projection parser on a 
saved fixture; qualitative gap rule; €STR step path; desk panels for EUR 
(200 with data / unavailable states); USD desk unchanged. Full suite, 
nothing deselected. 
 
DO NOT: other desks; LLM extraction; change Fed logic; new Python deps. 
DELIVER: REPORT_STEP9.md (≤60 lines). 
 
Work one part at a time: implement, run only the tests for that part, 
commit, update PROGRESS.md. Run the full suite once at the end of each 
part. Stop after Part B and give me a status of at most 10 lines; I will 
then start a fresh session for Parts C–F.
