(() => {
    const root = document.querySelector("[data-currency-research]");
    if (!root) return;

    const storageKey = "macroCurrencyResearchV2";
    const draftChartImages = [];
    const toneRank = { bearish: -1, neutral: 0, bullish: 1 };
    const toneLabels = { bearish: "Bearish", neutral: "Neutral", bullish: "Bullish" };

    const checklistText = {
        USD: `
Tier 1 - Global Risk Regime / Safe-Haven Demand
Risk-off shocks (geopolitical, financial stress) - USD is the world's primary safe haven and reserve currency; in genuine risk-off, capital floods into USD regardless of US-specific fundamentals
Global growth scares - When global growth wobbles, USD often strengthens even against weak US-specific data because it is the liquidity destination
VIX / credit stress indicators - Rising cross-asset stress typically pulls USD higher via deleveraging and USD-denominated liability coverage
Tier 2 - Fed Policy Path
FOMC rate decisions/tone - The single largest lever on USD; hawkish surprises strengthen USD via yield attraction, dovish surprises weaken it
Fed Chair communication style/credibility - Reduced forward guidance can become a volatility driver because markets cannot fully pre-price FOMC outcomes
Dot plot / market-implied rate path - Priced expectations move USD ahead of actual decisions; expected cuts or hikes matter more than the current rate level
FOMC voting composition/dissents - Split votes signal internal disagreement and potential future surprises
Tier 3 - US Inflation & Growth Data
CPI (headline + core) - Directly shapes the Fed's hike/hold/cut calculus
Employment (NFP, unemployment rate, wage growth) - Strong jobs data reduces the case for Fed cuts, supporting USD; weak data does the reverse
GDP growth - US growth relative to other G10 economies drives capital allocation toward or away from USD assets
PCE - The Fed's preferred inflation gauge and often more market-moving for Fed-watchers specifically
Tier 4 - Relative Growth & Policy Divergence
US growth vs Eurozone/Japan/UK growth - USD strength is often about relative outperformance, not absolute US strength
Fed policy vs ECB/BoJ/BoE policy paths - When the Fed cuts while peers hold or hike, USD weakens on narrowing yield advantage
Global capital flows into US equities/bonds - Persistent foreign inflows into US assets provide structural USD support independent of rate differentials
Tier 5 - Fiscal & Debt Dynamics
US fiscal deficit trajectory - Large deficits can eventually weigh on USD via long-term debt-sustainability concerns
Treasury issuance volume - Heavy issuance can lift yields short-term but raises longer-term sustainability questions
Debt ceiling / government funding events - Episodic political risk that can cause sharp but usually temporary USD volatility
Tier 6 - Trade Policy
Tariff policy - Tariffs can be USD-supportive short-term but USD-negative longer-term if they damage global trade and growth
US trade balance/deficit - A widening deficit is a structural USD headwind
Tier 7 - Energy & Commodity Prices
Oil prices - Higher oil can support USD via hawkish Fed implications or pressure it through stagflation fears, depending on context
Geopolitical energy shocks - The US energy-producer status softens but does not eliminate the inflation-growth tradeoff
Tier 8 - Positioning & Sentiment
CFTC COT - USD/DXY positioning - Extreme long or short USD positioning signals a crowded trade vulnerable to unwind
Real money vs speculative positioning divergence - Reserve-manager and speculative-flow divergence can flag turning points
Retail sentiment - Standard contrarian read at extremes
Tier 9 - Technicals
DXY structural levels - Multi-year range boundaries attract algorithmic and institutional flow
DXY vs yield-differential correlation - Decoupling usually signals a non-rate story temporarily dominating
Momentum/trend indicators - Timing only, not directional bias
`,
        EUR: `
Tier 1 - Geopolitical / Risk Regime
Active geopolitical shock (energy/trade) - Europe imports most of its energy, so Middle East or Russia-linked energy shocks pressure EUR terms of trade
VIX / broad risk sentiment - EUR can sell off in acute risk-off if Europe is perceived as more exposed than the US
US-China / global trade tensions - Eurozone growth is export-heavy; disruption hits German/French manufacturing and industrial output
Tier 2 - Energy Prices
Natural gas prices (TTF benchmark) - Rising gas prices are a direct EUR negative via import costs and inflation
Oil prices - Rising oil raises import costs and squeezes European consumers/industry, generally EUR-negative
Energy supply security - Pipeline, LNG, or transit disruption raises the EUR risk premium
Tier 3 - CB Policy Divergence (ECB vs Fed)
ECB decision/tone vs prior - A hawkish ECB surprise raises Eurozone yields and strengthens EUR; dovish surprise does the reverse
ECB-Fed rate/yield differential - Widening in Europe's favor is bullish EUR
Market-implied ECB path vs Fed path - Future divergence expectations often matter more than today's spot rate gap
ECB officials' speeches - Lagarde and key officials can reprice rate expectations between meetings
Tier 4 - Economic Data
Eurozone CPI (headline + core) - The biggest data lever on ECB rate expectations
Germany PMI/ifo/ZEW surveys - Germany is the manufacturing bellwether for the Eurozone growth narrative
Eurozone GDP - Broad growth signal feeding the ECB reaction function
Unemployment / wage growth - Wage growth is central to ECB services-inflation concern
Compare vs US data - EUR/USD often trades relative surprise rather than the absolute number
Tier 5 - Trade & Fiscal-Political Cohesion
Eurozone trade balance - A surplus supports EUR; a deficit during energy shocks weighs on it
US tariff policy toward EU - Direct threat to European export revenue
Peripheral bond spreads - Italy/France vs Germany spread widening signals fiscal or political stress
EU political stability - France/Germany fragmentation raises the currency-union risk premium
Tier 6 - Banking & Financial System Health
European bank health/stress indicators - Banking stress transmits quickly into the real economy
Credit growth/lending conditions - Tight credit signals slower growth and a more dovish ECB bias
Tier 7 - Structural Growth Factors
German industrial output - Auto and competitiveness weakness is a persistent EUR headwind
Demographics / productivity trends - Shapes the multi-year EUR growth ceiling
Tier 8 - Positioning & Sentiment
CFTC COT - EUR futures positioning - Extreme positioning signals crowded-trade squeeze risk
Retail sentiment - Contrarian read at extremes
EUR/CHF trend - Cleaner read on Eurozone stress than EUR/USD because CHF is the regional safe haven
Tier 9 - Technicals
EUR/USD structural levels - Multi-year boundaries attract algorithmic and institutional flow
EUR/USD vs rate-differential correlation - Decoupling suggests energy, political, or other non-rate stories dominate
Momentum/trend indicators - Timing only, not directional bias
`,
        GBP: `
Tier 1 - Global Risk Regime
Risk-off shocks - GBP is not a true safe haven; in acute risk-off it tends to underperform because of the UK's twin-deficit profile
VIX / broad risk sentiment - Elevated stress often hits GBP disproportionately because of London's financial-center exposure
US-China / global trade tensions - Broad trade disruption is a UK growth headwind
Tier 2 - BoE Policy Path
MPC rate decisions/tone - Primary lever on GBP; hawkish surprises strengthen GBP
MPC vote split - A shifting vote signals the committee's internal lean before the headline rate changes
BoE-Fed / BoE-ECB rate differential - Direct driver of relative capital flows
Market-implied BoE path (SONIA futures) - Priced expectations move GBP ahead of decisions
Bailey and MPC speeches - Can shift rate expectations between meetings
Tier 3 - UK Inflation & Growth Data
CPI (headline + core + services) - Sticky services inflation keeps the BoE cautious
Employment / wage growth - Wage growth feeds directly into services-inflation concern
GDP growth - Chronic weak growth versus peers is a structural GBP headwind
Compare vs Eurozone/US data - GBP trades relative surprise against both blocs
Tier 4 - Fiscal Policy & Gilt Market
UK budget/fiscal statements - Fiscal credibility is a live structural GBP risk after the mini-budget precedent
Gilt yields / gilt market stress - Rising yields may be hawkish repricing or fiscal-credibility stress; causation matters
OBR forecasts and fiscal headroom - Tight headroom raises odds of tax rises or spending cuts
Tier 5 - Trade & Post-Brexit Dynamics
UK trade balance - Persistent deficit is a structural GBP headwind
EU-UK trade relationship developments - Changes in EU friction have outsized impact
US-UK trade policy - Tariff headlines matter at the margin
Tier 6 - Financial Sector / London Hub
UK financial sector health - Banking and financial stress quickly affects GBP sentiment
Foreign investment flows into UK assets - GBP relies on capital-account inflows to fund current-account vulnerability
Tier 7 - Political Stability
Government stability / political risk - Leadership churn and policy U-turns make political risk prominent
Election cycles / major policy shifts - Can trigger sharp repricing if credibility is questioned
Tier 8 - Positioning & Sentiment
CFTC COT - GBP futures positioning - Extreme positioning signals squeeze risk
Retail sentiment - Contrarian read at extremes
EUR/GBP trend - Cleaner read on UK-specific versus Eurozone-specific stress
Tier 9 - Technicals
GBP/USD and EUR/GBP structural levels - Multi-year boundaries attract institutional and algorithmic flow
GBP/USD vs rate-differential correlation - Decoupling flags fiscal, political, or other non-rate dominance
Momentum/trend indicators - Timing only
`,
        JPY: `
Tier 1 - Global Risk Regime
Risk-off shocks - JPY is a genuine safe haven; risk-off drives JPY buying as carry trades unwind
Global equity direction - Falling equities trigger carry-trade unwinds and push JPY stronger
VIX / cross-asset volatility spikes - Vol spikes directly threaten leveraged carry positions and support JPY
Tier 2 - BoJ Policy Path
BoJ rate decisions/tone - Hawkish surprises strengthen JPY because normalization starts from a low base
BoJ-Fed / BoJ-peer rate differential - Narrowing the gap can trigger outsized JPY moves
Board vote splits / dissent - Hawkish dissent signals the committee's internal lean is shifting
Governor Ueda communication - Comments on not falling behind the curve can move JPY as much as a decision
Yield curve control / JGB policy - Balance-sheet policy affects JPY through the bond-yield channel
Tier 3 - Japanese Inflation & Wage Data
Core CPI - Key data point for BoJ normalization pace
Wage growth - Shunto and wage trends support sustainable inflation and continued hiking
Import price pass-through - Weak yen raises import inflation and can force BoJ action
Tier 4 - Government/MOF Intervention Risk
USD/JPY level vs intervention thresholds - Approaching historical zones raises event-driven intervention risk
Verbal intervention from MOF/Finance Minister - Jawboning often precedes actual intervention
Rate checks by MOF - Bank polling is a technical precursor signal
Coordinated intervention signals - US Treasury involvement hints would be rare but highly impactful
Tier 5 - Carry Trade Dynamics
Global rate differential level - JPY is the default funding currency and highly sensitive to global risk appetite
Implied/realized FX volatility - Low vol favors carry; vol spikes trigger JPY-positive unwinds
AUD/JPY, NZD/JPY as risk barometers - Crosses can signal unwind risk before USD/JPY
Tier 6 - Japanese Economic & Trade Data
GDP growth - Feeds into BoJ confidence to continue hiking
Trade balance - Energy import costs can flip the balance negative and pressure JPY
Current account balance - Japan's net foreign asset position supports repatriation during stress
Tier 7 - Fiscal Policy & Political Dynamics
Government debt sustainability concerns - Sharp JGB yield moves can trigger acute JPY volatility
Political stability / PM leadership - Leadership and BoJ appointment signals can shift policy direction
Tier 8 - Positioning & Sentiment
CFTC COT - JPY futures positioning - Extreme net-short JPY signals squeeze/unwind risk
Retail sentiment - Contrarian read at extremes
Options market skew - Demand for JPY upside protection can flag unwind fear
Tier 9 - Technicals
USD/JPY structural levels - Round numbers often overlap with intervention-risk zones
USD/JPY vs rate-differential correlation - Decoupling flags intervention or risk-sentiment dominance
Momentum/trend indicators - Timing only
`,
        AUD: `
Tier 1 - Global Risk Regime / China Linkage
Risk-off shocks - AUD is one of the highest-beta G10 currencies and sells off hard in risk-off
China growth/policy signals - China stimulus, PMI, and property stress move AUD through commodity demand
Global equity direction - AUD tracks Asian and Chinese risk sentiment closely
Tier 2 - Commodity Prices
Iron ore prices - Australia's largest export earner and the cleanest AUD commodity correlation
Coal, LNG prices - Secondary but meaningful export earners
Broader industrial metals - Confirms whether AUD strength is broader than iron ore
Tier 3 - RBA Policy Path
RBA cash rate decisions/tone - Hawkish surprises strengthen AUD via yield attraction
RBA-Fed / RBA-peer rate differential - Shapes carry appetite and capital flows
Market-implied RBA path - Priced expectations move AUD ahead of decisions
Quarterly Statement on Monetary Policy - Forecast revisions can move AUD more than standard meetings
Governor Bullock press conference tone - Can shift expectations independent of the decision
Tier 4 - Australian Economic Data
CPI (headline + trimmed mean) - Directly shapes RBA policy calculus
Employment / wage growth - Resilient labor supports hawkish RBA pricing
GDP growth - Broad growth signal for RBA reaction function
Compare vs China data - AUD often trades China's surprises more than Australia's own
Tier 5 - Trade & Terms of Trade
Australia trade balance - Commodity surpluses support AUD via net inflows
Terms of trade index - Improving export/import price ratio is structurally AUD-positive
China-Australia trade relations - Tariffs or import bans can hit sentiment
Tier 6 - Housing Market
Housing prices / household debt - Housing stress can force RBA caution
Housing credit growth - Rapid credit growth can become a policy concern
Tier 7 - Fiscal Policy
Federal budget balance - Slower-moving long-term credibility factor
State-level fiscal health - Secondary factor tied to commodity price cycles
Tier 8 - Positioning & Sentiment
CFTC COT - AUD futures positioning - Extreme positioning signals squeeze risk
Retail sentiment - Contrarian read at extremes
AUD/JPY trend - Classic G10 risk barometer cross
Tier 9 - Technicals
AUD/USD structural levels - Multi-year boundaries attract algorithmic and institutional flow
AUD/USD vs iron ore correlation - Decoupling flags non-commodity dominance
Momentum/trend indicators - Timing only
`,
        CAD: `
Tier 1 - Geopolitical / Risk Regime
Active geopolitical shock - Supply-threatening conflicts spike oil and risk-off sentiment simultaneously, often overriding CAD fundamentals
VIX / risk sentiment - CAD is high-beta; risk-off triggers CAD selling as capital flees to USD/JPY/CHF
Global equity direction - Equities up means risk appetite up and capital flows into commodity/high-beta currencies
Tier 2 - Oil / Commodity Complex
WTI crude trend - Rising oil means more USD inflow converted to CAD and supports CAD
WCS-WTI differential - A widening discount weakens the Canadian terms-of-trade story
OPEC+ decisions / US shale supply - Changes the global oil supply balance CAD tracks
Broader commodity basket - Confirms whether CAD strength is oil-specific or broader
Tier 3 - CB Policy Divergence (BoC vs Fed)
BoC decision/tone vs prior - Hawkish surprise strengthens CAD; dovish surprise does the reverse
BoC-Fed 2yr yield differential - Direct driver of rate-based capital flows into/out of CAD
Market-implied BoC path vs Fed - Expected future divergence moves CAD before decisions happen
BoC officials' speeches - Can reprice rate expectations between meetings
Tier 4 - Economic Data
Employment - Full-time/part-time mix and wages feed BoC expectations
CPI - Headline plus BoC core measures directly shape rate expectations
GDP - Broad growth signal; strong GDP supports hawkish BoC stance
Business Outlook Survey - Forward-looking sentiment BoC explicitly watches
Retail sales / housing starts - Consumer and housing health feed the BoC outlook
Compare vs US data - CAD often reacts to relative surprise because of trade linkage
Tier 5 - Trade & US Linkage
Canadian trade balance - A widening surplus supports CAD via export USD conversion
US ISM Manufacturing/Services - Leading indicator for US demand for Canadian exports
USMCA/tariff headline risk - Tariff threats directly threaten export revenue
US consumer spending - Strong US consumer means stronger Canadian export demand
Tier 6 - Housing & Household Debt
Housing starts / sales / prices - Cooling housing signals fragility and BoC caution
Household debt-to-income - High debt makes consumers rate-sensitive
Mortgage renewal wall - Higher resets squeeze spending and can force dovishness
Tier 7 - Fiscal
Federal budget trajectory - Large deficits can weigh on long-term currency credibility
Alberta fiscal health - Oil-linked provincial stress compounds during oil downturns
Sovereign rating outlook - Downgrades raise borrowing costs and can trigger outflows
Tier 8 - Positioning & Sentiment
CFTC COT positioning - Extreme CAD positioning signals crowded-trade reversal risk
Retail sentiment - Contrarian signal at extremes
CAD/JPY trend - Cleaner read on carry-on/carry-off than USD/CAD
Tier 9 - Technicals
USD/CAD structural levels - Multi-year boundaries amplify institutional flow
USD/CAD vs WTI correlation - Decoupling signals non-oil dominance
Momentum/trend indicators - Timing only
`,
        CHF: `
Tier 1 - Global Risk Regime
Risk-off shocks - CHF is one of the purest G10 safe havens; risk-off often overrides weak fundamentals
European-specific stress - CHF benefits from banking, sovereign, or political stress within Europe
Global equity direction / VIX - Falling risk appetite pulls capital into CHF
Tier 2 - SNB Policy Path
SNB rate decisions/tone - With policy near zero, negative-rate risk is the distinct CHF framing
SNB-ECB / SNB-Fed rate differential - Driver of relative capital flows and carry
SNB currency intervention rhetoric/action - FX intervention is a distinct SNB tool when CHF strength threatens exports
President Schlegel communication on negative rates - Any change to the high-barrier stance matters
Quarterly Monetary Policy Assessment - Inflation forecast revisions shape rate expectations
Tier 3 - Swiss Inflation & Growth Data
Swiss CPI - Soft inflation raises negative-rate odds; pickup reduces them
Imported goods prices - Strong CHF suppresses imported inflation and creates feedback loops
GDP growth - Weakness supports continued accommodation
Tier 4 - Export Competitiveness & Trade Policy
US tariff policy toward Switzerland - Higher tariffs versus EU exports create a direct competitive disadvantage
Swiss trade balance - Strong historically, but tariffs and CHF strength can erode it
EUR/CHF specifically - Key gauge of Swiss export competitiveness
Tier 5 - Banking Sector Health
Swiss banking sector stability - UBS/systemic stress has outsized impact because the sector is large relative to GDP
Wealth management / private banking flows - Structural inflows support CHF independent of rate differentials
Tier 6 - Fiscal Policy
Swiss federal budget balance - Debt-brake credibility makes fiscal risk structurally quiet for CHF
Cantonal fiscal health - Secondary, low-impact factor
Tier 7 - Structural/Political Factors
Swiss political stability - Consensus politics is a source of quiet strength
EU-Switzerland relationship developments - Matters at the margin for trade-sensitive sectors
Tier 8 - Positioning & Sentiment
CFTC COT - CHF futures positioning - Crowded long-CHF can reverse sharply on de-escalation
Retail sentiment - Contrarian read at extremes
EUR/CHF and USD/CHF divergence - Helps separate safe-haven strength from EUR- or USD-specific weakness
Tier 9 - Technicals
USD/CHF and EUR/CHF structural levels - Psychological zones matter, especially rare USD/CHF areas
Correlation with global risk indicators - Divergence usually signals SNB or intervention story dominance
Momentum/trend indicators - Timing only
`,
        NZD: `
Tier 1 - Global Risk Regime / China & Australia Linkage
Risk-off shocks - NZD is extremely high beta and sells off fast in risk-off
China growth/demand signals - China demand data moves NZD through dairy and export demand
Australian economic conditions - AUD weakness often drags NZD due to tight linkages
Tier 2 - Commodity Prices
Global dairy prices - Dairy is NZD's version of oil-for-CAD or iron-ore-for-AUD
Meat, forestry, wine export prices - Secondary agricultural export earners
Broader agricultural commodity cycle - Confirms whether NZD strength is dairy-specific
Tier 3 - RBNZ Policy Path
RBNZ OCR decisions/tone - Primary lever; RBNZ cycles are often sharper than peers
RBNZ-Fed / RBNZ-peer rate differential - Driver of carry appetite and flows
Market-implied OCR path - Priced expectations move NZD before decisions
Monetary Policy Statement - Quarterly forecast revisions often move NZD more than interim reviews
RBNZ Governor guidance - Explicit forward guidance can have outsized market impact
Tier 4 - New Zealand Economic Data
CPI - Directly shapes RBNZ policy calculus
Employment / wage growth - Labor tightness feeds inflation outlook
GDP growth - Small open economy is prone to sharper growth swings
Compare vs China and Australia data - Regional partner surprises can dominate domestic data
Tier 5 - Trade & Terms of Trade
NZ trade balance - More sensitive to swings than Australia's commodity-surplus profile
Terms of trade index - Dairy swings move export/import price ratios meaningfully
China-NZ trade relationship - Demand or diplomatic shifts have outsized impact
Tier 6 - Housing Market
Housing prices / household debt - Housing sensitivity can drive RBNZ swings
Mortgage rate reset dynamics - Higher resets can force RBNZ caution
Tier 7 - Fiscal Policy
Government budget balance - Slower-moving, lower-impact factor
Fiscal stimulus/austerity signals - Can shift growth expectations at the margin
Tier 8 - Positioning & Sentiment
CFTC COT - NZD futures positioning - Thinner liquidity means extremes can produce sharper squeezes
Retail sentiment - Contrarian read at extremes
NZD/JPY trend - Secondary high-beta risk-barometer cross
AUD/NZD cross specifically - Isolates NZ-specific versus Australia-specific policy divergence
Tier 9 - Technicals
NZD/USD structural levels - Multi-year boundaries; thinner liquidity can make moves sharper
NZD/USD vs dairy-price correlation - Decoupling flags non-commodity dominance
Momentum/trend indicators - Timing only
`,
    };

    const appendCommonSections = (currency, tiers) => [
        ...tiers,
        ["Interest rate probability", [
            [`${currency.toLowerCase()}_rate_probability`, "Latest market-implied policy probability", "Track the next-meeting and 12-month implied path; mark bullish when pricing shifts toward a more hawkish path versus peers."],
            [`${currency.toLowerCase()}_rate_probability_change`, "Daily change in rate probability", "Note whether the probability move confirms or contradicts the checklist tone."],
        ]],
        ["Other points", [
            [`${currency.toLowerCase()}_other_1`, "Other market point", "Use for one-off catalysts, bank research, unusual flow, chart structure, or anything not captured above."],
            [`${currency.toLowerCase()}_other_2`, "Second other point", "Optional extra point for the day's alpha note."],
        ]],
    ];

    function itemId(currency, title) {
        return `${currency.toLowerCase()}_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 44)}`;
    }

    function parseChecklist(currency, raw) {
        const tiers = [];
        let active = null;
        raw.trim().split("\n").map((line) => line.trim()).filter(Boolean).forEach((line) => {
            const tierMatch = line.match(/^Tier\s+\d+\s+-\s+(.+)$/i);
            if (tierMatch) {
                active = [tierMatch[1], []];
                tiers.push(active);
                return;
            }
            if (!active) return;
            const parts = line.split(/\s+-\s+/);
            const title = parts.shift() || line;
            const detail = parts.join(" - ");
            active[1].push([itemId(currency, title), title, detail]);
        });
        return appendCommonSections(currency, tiers);
    }

    const checklists = Object.fromEntries(
        Object.entries(checklistText).map(([currency, raw]) => [currency, parseChecklist(currency, raw)])
    );

    const els = {
        currency: root.querySelector("[data-research-currency]"),
        date: root.querySelector("[data-research-date]"),
        overallTone: root.querySelector("[data-research-overall-tone]"),
        checklist: root.querySelector("[data-research-checklist]"),
        lastUpdated: root.querySelector("[data-research-last-updated]"),
        currentTone: root.querySelector("[data-current-tone]"),
        currentToneDetail: root.querySelector("[data-current-tone-detail]"),
        toneChange: root.querySelector("[data-tone-change]"),
        toneChangeDetail: root.querySelector("[data-tone-change-detail]"),
        toneBalance: root.querySelector("[data-tone-balance]"),
        historyList: root.querySelector("[data-tone-history-list]"),
        chartInput: root.querySelector("[data-chart-input]"),
        chartPreviewList: root.querySelector("[data-chart-preview-list]"),
        dropzone: root.querySelector("[data-chart-dropzone]"),
    };

    const today = () => new Date().toISOString().slice(0, 10);
    const esc = (value) => String(value || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    const loadStore = () => JSON.parse(localStorage.getItem(storageKey) || "{}");
    const saveStore = (store) => localStorage.setItem(storageKey, JSON.stringify(store));
    const activeChecklist = () => checklists[els.currency.value] || checklists.CAD;
    const checklistItems = () => activeChecklist().flatMap(([, items]) => items);
    const currencyStore = () => {
        const store = loadStore();
        store[els.currency.value] ||= { snapshots: [] };
        return store;
    };

    const emptyDraft = () => ({
        currency: els.currency.value,
        date: els.date.value || today(),
        overallTone: "neutral",
        fields: { summary: "", alpha: "", risks: "" },
        items: Object.fromEntries(checklistItems().map(([id]) => [id, { tone: "neutral", notes: "" }])),
    });

    let draft = emptyDraft();

    function toneBalance(items) {
        const values = Object.values(items || {});
        return {
            bullish: values.filter((item) => item.tone === "bullish").length,
            neutral: values.filter((item) => item.tone === "neutral").length,
            bearish: values.filter((item) => item.tone === "bearish").length,
        };
    }

    function latestSnapshots() {
        const store = loadStore();
        return [...(store[els.currency.value]?.snapshots || [])].sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
    }

    function toneChangeText(current, prior) {
        if (!prior) return ["Flat", "No prior snapshot to compare yet."];
        const delta = toneRank[current.overallTone] - toneRank[prior.overallTone];
        const changedItems = checklistItems()
            .filter(([id]) => current.items?.[id]?.tone !== prior.items?.[id]?.tone)
            .map(([id, title]) => `${title}: ${toneLabels[prior.items?.[id]?.tone || "neutral"]} -> ${toneLabels[current.items?.[id]?.tone || "neutral"]}`);
        if (delta > 0) return ["More bullish", changedItems.slice(0, 3).join("; ") || "Overall tone improved."];
        if (delta < 0) return ["More bearish", changedItems.slice(0, 3).join("; ") || "Overall tone deteriorated."];
        return ["Flat", changedItems.slice(0, 3).join("; ") || "No checklist tone changes."];
    }

    function renderChecklist() {
        els.checklist.innerHTML = activeChecklist().map(([tier, items], tierIndex) => `
            <section class="research-tier">
                <header>
                    <span>${tier.startsWith("Interest") || tier.startsWith("Other") ? "Extra" : `Tier ${tierIndex + 1}`}</span>
                    <strong>${esc(tier)}</strong>
                </header>
                ${items.map(([id, title, detail]) => {
                    const item = draft.items[id] || { tone: "neutral", notes: "" };
                    return `
                        <article class="research-check">
                            <div>
                                <strong>${esc(title)}</strong>
                                <small>${esc(detail)}</small>
                            </div>
                            <select data-item-tone="${esc(id)}">
                                <option value="neutral" ${item.tone === "neutral" ? "selected" : ""}>Neutral</option>
                                <option value="bullish" ${item.tone === "bullish" ? "selected" : ""}>Bullish</option>
                                <option value="bearish" ${item.tone === "bearish" ? "selected" : ""}>Bearish</option>
                            </select>
                            <textarea data-item-notes="${esc(id)}" placeholder="Notes for this point">${esc(item.notes)}</textarea>
                        </article>
                    `;
                }).join("")}
            </section>
        `).join("");
    }

    function renderCharts() {
        els.chartPreviewList.innerHTML = draftChartImages.length ? draftChartImages.map((chart, index) => `
            <figure class="chart-preview">
                <img src="${chart.dataUrl}" alt="${esc(chart.name)}">
                <figcaption>${esc(chart.name)} <button type="button" data-remove-chart="${index}">Remove</button></figcaption>
            </figure>
        `).join("") : '<div class="chart-preview-empty">No charts in the current report draft.</div>';
    }

    function renderSummary() {
        const snapshots = latestSnapshots();
        const latest = snapshots[0];
        const prior = snapshots.find((item) => item.date !== draft.date);
        const balance = toneBalance(draft.items);
        const [change, detail] = toneChangeText(draft, prior);

        els.currentTone.textContent = toneLabels[draft.overallTone || "neutral"];
        els.currentTone.className = `tone-${draft.overallTone || "neutral"}`;
        els.currentToneDetail.textContent = latest ? `Draft for ${draft.date}. Last saved ${new Date(latest.updatedAt).toLocaleString()}.` : `No saved ${els.currency.value} snapshot yet.`;
        els.toneChange.textContent = change;
        els.toneChangeDetail.textContent = detail;
        els.toneBalance.textContent = `${balance.bullish} / ${balance.neutral} / ${balance.bearish}`;
        els.lastUpdated.textContent = latest ? new Date(latest.updatedAt).toLocaleString() : "Never";
        els.historyList.innerHTML = snapshots.length ? snapshots.slice(0, 8).map((item, index) => {
            const [changeLabel] = toneChangeText(item, snapshots[index + 1]);
            return `<button type="button" data-load-snapshot="${esc(item.id)}"><strong>${esc(item.date)} - ${toneLabels[item.overallTone]}</strong><span>${esc(changeLabel)}</span></button>`;
        }).join("") : '<div class="chart-preview-empty">No saved snapshots yet.</div>';
    }

    function hydrateForm() {
        els.date.value = draft.date || today();
        els.overallTone.value = draft.overallTone || "neutral";
        root.querySelectorAll("[data-research-field]").forEach((field) => {
            field.value = draft.fields?.[field.dataset.researchField] || "";
        });
        renderChecklist();
        renderSummary();
    }

    function readForm() {
        draft.currency = els.currency.value;
        draft.date = els.date.value || today();
        draft.overallTone = els.overallTone.value || "neutral";
        root.querySelectorAll("[data-research-field]").forEach((field) => {
            draft.fields[field.dataset.researchField] = field.value;
        });
        root.querySelectorAll("[data-item-tone]").forEach((field) => {
            draft.items[field.dataset.itemTone] ||= { tone: "neutral", notes: "" };
            draft.items[field.dataset.itemTone].tone = field.value;
        });
        root.querySelectorAll("[data-item-notes]").forEach((field) => {
            draft.items[field.dataset.itemNotes] ||= { tone: "neutral", notes: "" };
            draft.items[field.dataset.itemNotes].notes = field.value;
        });
    }

    function saveSnapshot() {
        readForm();
        const store = currencyStore();
        const bucket = store[draft.currency];
        const snapshot = {
            ...JSON.parse(JSON.stringify(draft)),
            id: `${draft.currency}-${draft.date}-${Date.now()}`,
            updatedAt: new Date().toISOString(),
        };
        bucket.snapshots = [snapshot, ...bucket.snapshots.filter((item) => item.date !== draft.date)].slice(0, 120);
        saveStore(store);
        renderSummary();
    }

    function loadSnapshot(id) {
        const snapshot = latestSnapshots().find((item) => item.id === id);
        if (!snapshot) return;
        draft = JSON.parse(JSON.stringify(snapshot));
        els.currency.value = draft.currency;
        hydrateForm();
    }

    function clearDraft() {
        draft = emptyDraft();
        draftChartImages.length = 0;
        renderCharts();
        hydrateForm();
    }

    function switchCurrency() {
        draft = emptyDraft();
        draftChartImages.length = 0;
        renderCharts();
        hydrateForm();
    }

    function readFiles(files) {
        Array.from(files || []).filter((file) => file.type.startsWith("image/")).forEach((file) => {
            const reader = new FileReader();
            reader.onload = () => {
                draftChartImages.push({ name: file.name || "Pasted chart", type: file.type || "image/png", dataUrl: String(reader.result || "") });
                renderCharts();
            };
            reader.readAsDataURL(file);
        });
    }

    async function exportPdf() {
        readForm();
        const Pdf = window.jspdf?.jsPDF;
        if (!Pdf) {
            window.print();
            return;
        }
        const pdf = new Pdf({ unit: "pt", format: "a4" });
        const pageWidth = pdf.internal.pageSize.getWidth();
        const pageHeight = pdf.internal.pageSize.getHeight();
        const margin = 40;
        let y = 46;

        const addPageIfNeeded = (heightNeeded) => {
            if (y + heightNeeded <= pageHeight - margin) return;
            pdf.addPage();
            y = 46;
        };
        const text = (value, x, size, style = "normal", color = [42, 48, 57]) => {
            pdf.setFont("helvetica", style);
            pdf.setFontSize(size);
            pdf.setTextColor(...color);
            const lines = pdf.splitTextToSize(String(value || ""), pageWidth - margin * 2 - (x - margin));
            lines.forEach((line) => {
                addPageIfNeeded(size + 8);
                pdf.text(line, x, y);
                y += size + 5;
            });
        };
        const section = (title) => {
            addPageIfNeeded(30);
            y += 10;
            text(title, margin, 12, "bold", [20, 92, 116]);
        };

        pdf.setFillColor(14, 23, 31);
        pdf.rect(0, 0, pageWidth, 96, "F");
        pdf.setTextColor(255, 255, 255);
        pdf.setFont("helvetica", "bold");
        pdf.setFontSize(21);
        pdf.text(`${draft.currency} Alpha Research`, margin, 42);
        pdf.setFont("helvetica", "normal");
        pdf.setFontSize(10);
        pdf.text(`${draft.date} - ${toneLabels[draft.overallTone]}`, margin, 64);
        y = 124;

        section("Executive Summary");
        text(draft.fields.summary || "No executive summary entered.", margin, 10);
        section("Final Alpha View");
        text(draft.fields.alpha || "No alpha view entered.", margin, 10);
        section("Risks / Invalidation");
        text(draft.fields.risks || "No risk notes entered.", margin, 10);

        section("Checklist");
        activeChecklist().forEach(([tier, items], tierIndex) => {
            addPageIfNeeded(34);
            const label = tier.startsWith("Interest") || tier.startsWith("Other") ? tier : `Tier ${tierIndex + 1} - ${tier}`;
            text(label, margin, 10, "bold", [38, 46, 56]);
            items.forEach(([id, title]) => {
                const item = draft.items[id] || {};
                const notes = item.notes ? ` - ${item.notes}` : "";
                text(`${toneLabels[item.tone || "neutral"]}: ${title}${notes}`, margin + 12, 8);
            });
        });

        if (draftChartImages.length) {
            section("Charts");
            for (const chart of draftChartImages) {
                addPageIfNeeded(270);
                text(chart.name || "Chart", margin, 9, "bold");
                try {
                    const imageType = String(chart.type || "").includes("jpeg") || String(chart.type || "").includes("jpg") ? "JPEG" : "PNG";
                    pdf.addImage(chart.dataUrl, imageType, margin, y, pageWidth - margin * 2, 220, undefined, "FAST");
                    y += 236;
                } catch (error) {
                    text("Chart could not be embedded in PDF.", margin, 9);
                }
            }
        }

        pdf.save(`${draft.currency.toLowerCase()}-alpha-research-${draft.date}.pdf`);
    }

    root.addEventListener("input", (event) => {
        if (event.target.matches("[data-research-field], [data-item-notes], [data-item-tone], [data-research-date], [data-research-overall-tone]")) {
            readForm();
            renderSummary();
        }
    });
    root.addEventListener("change", (event) => {
        if (event.target.matches("[data-research-currency]")) switchCurrency();
    });
    root.addEventListener("click", (event) => {
        if (event.target.closest("[data-save-research]")) saveSnapshot();
        if (event.target.closest("[data-export-research]")) exportPdf();
        if (event.target.closest("[data-chart-upload]")) els.chartInput.click();
        if (event.target.closest("[data-clear-draft]")) clearDraft();
        const remove = event.target.closest("[data-remove-chart]");
        if (remove) {
            draftChartImages.splice(Number(remove.dataset.removeChart), 1);
            renderCharts();
        }
        const load = event.target.closest("[data-load-snapshot]");
        if (load) loadSnapshot(load.dataset.loadSnapshot);
    });
    els.chartInput.addEventListener("change", () => readFiles(els.chartInput.files));
    els.dropzone.addEventListener("paste", (event) => {
        readFiles(event.clipboardData?.files);
    });
    document.addEventListener("paste", (event) => {
        if (!root.contains(document.activeElement)) return;
        readFiles(event.clipboardData?.files);
    });

    els.currency.value = "CAD";
    els.date.value = today();
    hydrateForm();
    renderCharts();
})();
