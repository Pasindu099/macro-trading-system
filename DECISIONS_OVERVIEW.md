OVERVIEW v1 (save as DECISIONS_OVERVIEW.md; commit per part)
Spec: design/design-mockups/Overview.dc.html. Match its layout, sections,
order and states. Ignore its illustrative numbers.

PART A — SHELL RESTYLE (all pages)
- Replace the sidebar with the mockup's top navigation bar: Overview,
  Desks, Pairs, Central Banks, News, Calendar (Event Log under it),
  Positioning, Data. Mobile: collapsible menu.
- Accent = amber (#f2a33a, text #f6b65a). Remove the lime-green accent
  everywhere. Amber = hawkish / up / stronger, blue = dovish / down /
  weaker; never use amber as decoration.
- Desks USD/EUR must look unchanged apart from the nav.

PART B — OVERVIEW SECTIONS (lazy-loaded panels, same pattern as desks)
1. Header: dominant theme (template text from verdicts + situations, no
   LLM) + dominance hierarchy (6 tiers, from the Step 10 rules).
2. Active situations: all active episodes, linking to the desk/pair.
3. What's moving markets: high-impact items, last 24h, from news_alerts /
   raw_news with rule-based tags; "AI notes paused" where relevant.
4. Currency ranking (all 8): Macro State composite, COT positioning,
   curve regime. Bias/CB regime/gap/thesis from verdicts for USD and EUR;
   "desk pending" for the other six. Row links: /desks/{ccy} if enabled.
5. Central bank regime map: Fed and ECB from the regime model; the other
   six "pending" with their next meeting date from config.
6. Pair map: overall-score lens for all 28 from Macro State; policy-
   divergence lens only where both desks exist (EUR/USD), others greyed
   "pending"; concentration check as in the mockup.
7. Key events ahead: next high-impact events from the calendar.

PART C — QUALITY: same states as desks (loading, empty, unavailable,
pending, error); 60 s cache; responsive to 390 px.

TESTS: each panel 200 with data / pending states; no numbers in pending
cells; ranking sorted by composite; nav renders on every page.
DO NOT: new desks; change Macro State, verdict or situation logic.
DELIVER: ≤10-line status.
