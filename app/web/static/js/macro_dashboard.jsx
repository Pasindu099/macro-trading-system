const { useState, useEffect, useRef } = React;

// -- Fonts via Google ------------------------------------------
const fontLink = document.createElement("link");
fontLink.rel = "stylesheet";
fontLink.href = "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap";
document.head.appendChild(fontLink);

// -- Theme -----------------------------------------------------
const T = {
  navy:    "#000000",
  navyMid: "#1A1D24",
  gold:    "#F5A623",
  goldLight:"#F7C35F",
  cream:   "#E8EAF0",
  offWhite:"#F6F7FA",
  grey:    "#8892A0",
  greyLt:  "#C3CBD6",
  red:     "#E84040",
  green:   "#00C896",
  orange:  "#F9735B",
  blue:    "#4A9FE0",
  teal:    "#2AA6A1",
  purple:  "#9B8CFF",
  white:   "#FFFFFF",
};

const css = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: ${T.navy}; font-family: 'DM Sans', sans-serif; }

  .app { min-height: 100vh; background: ${T.navy}; color: ${T.cream}; }

  /* Sidebar */
  .sidebar {
    position: fixed; top: 0; left: 0; bottom: 0; width: 240px;
    background: #111318; border-right: 1px solid rgba(61,70,86,0.56);
    display: flex; flex-direction: column; z-index: 100;
    padding: 0;
  }
  .sidebar-logo {
    padding: 28px 24px 20px;
    border-bottom: 1px solid rgba(61,70,86,0.52);
  }
  .sidebar-logo-text {
    font-family: 'Playfair Display', serif; font-weight: 900;
    font-size: 15px; color: ${T.gold}; letter-spacing: 0.04em;
    line-height: 1.3; text-transform: uppercase;
  }
  .sidebar-logo-sub {
    font-size: 10px; color: ${T.grey}; letter-spacing: 0.12em;
    text-transform: uppercase; margin-top: 4px; font-weight: 500;
  }
  .sidebar-nav { flex: 1; padding: 16px 12px; overflow-y: auto; }
  .nav-section-label {
    font-size: 9px; color: ${T.grey}; letter-spacing: 0.14em;
    text-transform: uppercase; font-weight: 600;
    padding: 0 12px; margin: 16px 0 6px;
  }
  .nav-item {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 12px; border-radius: 6px; cursor: pointer;
    font-size: 13px; font-weight: 500; color: ${T.grey};
    transition: all 0.15s ease; margin-bottom: 2px;
    border: 1px solid transparent;
  }
  .nav-item:hover { background: rgba(74,159,224,0.08); color: ${T.cream}; }
  .nav-item.active {
    background: rgba(74,159,224,0.14); color: ${T.blue};
    border-color: rgba(74,159,224,0.28);
  }
  .nav-link { text-decoration: none; }
  .nav-item .nav-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: currentColor; opacity: 0.5; flex-shrink: 0;
  }
  .nav-item.active .nav-dot { opacity: 1; background: ${T.green}; }

  /* Main content */
  .main { margin-left: 240px; min-height: 100vh; padding: 32px 40px; max-width: 1100px; }

  /* Page header */
  .page-header { margin-bottom: 32px; }
  .page-title {
    font-family: 'Playfair Display', serif; font-weight: 900;
    font-size: 32px; color: ${T.cream}; line-height: 1.15;
  }
  .page-title span { color: ${T.gold}; }
  .page-subtitle { font-size: 13px; color: ${T.grey}; margin-top: 6px; font-weight: 400; }

  /* Cards */
  .card {
    background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px; padding: 24px; margin-bottom: 20px;
  }
  .card-header {
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 18px; padding-bottom: 14px;
    border-bottom: 1px solid rgba(255,255,255,0.06);
  }
  .card-number {
    font-family: 'DM Mono', monospace; font-size: 11px;
    color: ${T.gold}; letter-spacing: 0.08em; font-weight: 500;
  }
  .card-title {
    font-family: 'Playfair Display', serif; font-size: 16px;
    font-weight: 700; color: ${T.cream};
  }
  .card-desc { font-size: 12px; color: ${T.grey}; margin-top: 2px; }

  /* Form elements */
  label { display: block; font-size: 11px; font-weight: 600; color: ${T.grey};
    letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 6px; }
  input, textarea, select {
    width: 100%; background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 6px;
    color: ${T.cream}; font-family: 'DM Sans', sans-serif; font-size: 13px;
    padding: 9px 12px; outline: none; transition: border-color 0.15s;
    resize: vertical;
  }
  input:focus, textarea:focus, select:focus {
    border-color: rgba(74,159,224,0.48);
    background: rgba(74,159,224,0.05);
  }
  input::placeholder, textarea::placeholder { color: rgba(136,146,160,0.7); }
  select option { background: #111318; }

  .form-grid { display: grid; gap: 14px; }
  .form-grid.cols-2 { grid-template-columns: 1fr 1fr; }
  .form-grid.cols-3 { grid-template-columns: 1fr 1fr 1fr; }
  .form-grid.cols-4 { grid-template-columns: 1fr 1fr 1fr 1fr; }

  /* Buttons */
  .btn {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 10px 20px; border-radius: 6px; font-size: 13px;
    font-weight: 600; cursor: pointer; border: none; outline: none;
    transition: all 0.15s ease; font-family: 'DM Sans', sans-serif;
    letter-spacing: 0.02em;
  }
  .btn-primary {
    background: ${T.blue}; color: ${T.white};
  }
  .btn-primary:hover { background: #62AFE8; transform: translateY(-1px); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-ghost {
    background: transparent; color: ${T.grey};
    border: 1px solid rgba(255,255,255,0.1);
  }
  .btn-ghost:hover { border-color: rgba(74,159,224,0.36); color: ${T.blue}; }
  .btn-danger {
    background: rgba(232,64,64,0.15); color: ${T.red};
    border: 1px solid rgba(232,64,64,0.3);
  }
  .btn-danger:hover { background: rgba(232,64,64,0.25); }
  .btn-sm { padding: 6px 12px; font-size: 11px; }

  /* Event table */
  .event-table { width: 100%; border-collapse: collapse; }
  .event-table th {
    font-size: 10px; font-weight: 600; letter-spacing: 0.1em;
    text-transform: uppercase; color: ${T.grey}; padding: 8px 10px;
    text-align: left; border-bottom: 1px solid rgba(255,255,255,0.07);
  }
  .event-table td {
    padding: 10px 10px; font-size: 12.5px; color: ${T.cream};
    border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: middle;
  }
  .event-table tr:hover td { background: rgba(255,255,255,0.02); }
  .event-table input, .event-table select {
    padding: 5px 8px; font-size: 12px; margin: 0;
  }

  .brief-builder-table { width: 100%; border-collapse: collapse; }
  .brief-builder-table th {
    background: rgba(17,19,24,0.82); color: ${T.cream}; font-size: 10px;
    font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
    padding: 9px 8px; border: 1px solid rgba(61,70,86,0.62);
  }
  .brief-builder-table td {
    padding: 8px; border: 1px solid rgba(255,255,255,0.07);
    background: rgba(255,255,255,0.025); vertical-align: top;
  }
  .brief-builder-table textarea,
  .brief-builder-table input {
    min-height: 38px; padding: 7px 8px; font-size: 12px; margin: 0;
  }
  .brief-builder-table textarea { resize: vertical; }
  .brief-builder-table .mini-cell { min-width: 94px; }
  .brief-builder-table .wide-cell { min-width: 210px; }
  .brief-table-actions {
    display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px;
  }

  /* Impact badges */
  .badge {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase;
  }
  .badge-high { background: rgba(232,64,64,0.15); color: ${T.red}; border: 1px solid rgba(232,64,64,0.3); }
  .badge-med  { background: rgba(245,166,35,0.15); color: ${T.gold}; border: 1px solid rgba(245,166,35,0.3); }
  .badge-low  { background: rgba(136,146,160,0.15); color: ${T.grey}; border: 1px solid rgba(136,146,160,0.3); }

  /* Status / progress */
  .status-bar {
    background: rgba(74,159,224,0.08); border: 1px solid rgba(74,159,224,0.22);
    border-radius: 8px; padding: 14px 18px;
    display: flex; align-items: center; gap: 12px; margin-bottom: 20px;
  }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .status-dot.loading { background: ${T.gold}; animation: pulse 1.2s infinite; }
  .status-dot.success { background: ${T.green}; }
  .status-dot.idle    { background: ${T.grey}; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
  .status-text { font-size: 13px; color: ${T.cream}; flex: 1; }
  .status-sub  { font-size: 11px; color: ${T.grey}; margin-top: 2px; }

  /* Output preview */
  .output-box {
    background: #111318; border: 1px solid rgba(61,70,86,0.56);
    border-radius: 8px; padding: 20px; font-family: 'DM Mono', monospace;
    font-size: 11.5px; color: ${T.grey}; max-height: 360px; overflow-y: auto;
    line-height: 1.7; white-space: pre-wrap; word-break: break-word;
  }
  .output-box .line-gold { color: ${T.gold}; }
  .output-box .line-green { color: ${T.green}; }
  .output-box .line-grey { color: ${T.grey}; }

  /* Tab switcher */
  .tab-bar {
    display: flex; gap: 2px; background: rgba(0,0,0,0.2);
    padding: 4px; border-radius: 8px; margin-bottom: 24px; width: fit-content;
  }
  .tab-btn {
    padding: 8px 20px; border-radius: 6px; font-size: 12px; font-weight: 600;
    cursor: pointer; border: none; outline: none; transition: all 0.15s;
    font-family: 'DM Sans', sans-serif; letter-spacing: 0.03em;
    background: transparent; color: ${T.grey};
  }
  .tab-btn.active { background: ${T.navyMid}; color: ${T.goldLight}; }
  .tab-btn:hover:not(.active) { color: ${T.cream}; }

  /* Divider */
  .divider { height: 1px; background: rgba(255,255,255,0.06); margin: 24px 0; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(74,159,224,0.24); border-radius: 3px; }

  /* Action row */
  .action-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

  /* Week strip */
  .week-strip { display: flex; gap: 6px; margin-bottom: 18px; flex-wrap: wrap; }
  .week-day {
    padding: 5px 12px; border-radius: 5px; font-size: 11px; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase;
    background: rgba(255,255,255,0.04); color: ${T.grey};
    border: 1px solid rgba(255,255,255,0.07);
  }
  .week-day.has-events { background: rgba(245,166,35,0.10); color: ${T.gold}; border-color: rgba(245,166,35,0.28); }

  /* Fetch bar */
  .fetch-bar {
    display: flex; gap: 10px; align-items: flex-end; margin-bottom: 20px;
    background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.07);
    border-radius: 8px; padding: 16px;
  }
  .fetch-bar > div { flex: 1; }

  .helper-text { font-size: 11px; color: ${T.grey}; margin-top: 5px; }
  .section-sep { margin: 28px 0 20px; }
  .section-label {
    font-size: 10px; font-weight: 700; letter-spacing: 0.12em;
    text-transform: uppercase; color: ${T.gold}; margin-bottom: 14px;
    display: flex; align-items: center; gap: 8px;
  }
  .section-label::after { content:''; flex:1; height:1px; background:rgba(61,70,86,0.62); }

  .info-chip {
    display: inline-flex; align-items: center; gap: 5px;
    background: rgba(0,200,150,0.10); border: 1px solid rgba(0,200,150,0.28);
    border-radius: 5px; padding: 4px 10px; font-size: 11px; color: ${T.green};
  }

  .copy-btn {
    float: right; margin-top: -4px;
    background: rgba(74,159,224,0.10); border: 1px solid rgba(74,159,224,0.24);
    color: ${T.blue}; border-radius: 4px; padding: 3px 8px; font-size: 10px;
    cursor: pointer; font-family: 'DM Sans', sans-serif; font-weight: 600;
  }
`;

// -- Helpers ---------------------------------------------------
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
const REGIONS = ["US", "EU", "UK", "JP", "DE", "CA", "CH", "AU", "NZ", "CN"];
const IMPACTS = ["HIGH", "MED", "LOW"];

const DEFAULT_CONSENSUS_ROWS = [
  { id: 1, metric: "[Headline figure - e.g. NFP]", consensus: "[+XXXk]", prior: "[+XXXk]", range: "[+XXk to +XXXk]", why: "[Below 100k would suggest cooling; above 250k would challenge cut expectations]" },
  { id: 2, metric: "[Sub-component 1 - e.g. Avg Hourly Earnings m/m]", consensus: "[+X.X%]", prior: "[+X.X%]", range: "[X.X%-X.X%]", why: "[Wages above +0.4% m/m would reignite inflation concerns]" },
  { id: 3, metric: "[Sub-component 2 - e.g. Unemployment Rate]", consensus: "[X.X%]", prior: "[X.X%]", range: "[X.X%-X.X%]", why: "[Shows whether labour slack is building or tightening]" },
];

const DEFAULT_SCENARIO_ROWS = [
  { id: 1, outcome: "MUCH HOTTER", dataPrint: "[e.g. >+275k / >0.5% wages]", marketReaction: "[USD rallies sharply, UST yields spike, equities sell off, gold falls]", policyImplication: "[Market reprices cuts lower. Hawkish reassessment. Higher-for-longer narrative strengthens]", probability: "[X%]" },
  { id: 2, outcome: "SLIGHTLY HOT", dataPrint: "[e.g. +200k-+275k / wages inline]", marketReaction: "[Mild USD strength, yields edge higher, equities mixed. Growth stocks underperform]", policyImplication: "[Delays cut expectations by 1-2 meetings. Central bank likely stays data dependent]", probability: "[X%]" },
  { id: 3, outcome: "IN-LINE", dataPrint: "[e.g. +175k-+200k / wages as expected]", marketReaction: "[Muted immediate reaction. USD flat, yields unchanged, equities stable]", policyImplication: "[Consistent with base case. No change to rate path. Soft landing narrative intact]", probability: "[X%]" },
  { id: 4, outcome: "SLIGHTLY SOFT", dataPrint: "[e.g. +100k-+175k / wages miss]", marketReaction: "[USD softens, yields fall, equities initially rally, gold supported]", policyImplication: "[Adds to case for earlier cut. Market may re-price one additional cut]", probability: "[X%]" },
  { id: 5, outcome: "MUCH WEAKER", dataPrint: "[e.g. <+100k / rising unemployment]", marketReaction: "[USD sells off, yields fall sharply, equities volatile, gold rallies]", policyImplication: "[Emergency cut concerns surface. Market may price more aggressive easing]", probability: "[X%]" },
];

const DEFAULT_ASSET_ROWS = [
  { id: 1, assetClass: "USD (Dollar Index)", beat: "Up Strong", miss: "Down Strong", watch: "[DXY most sensitive. Real yield differentials drive USD. Watch EUR/USD and USD/JPY first]", tickers: "DXY, EUR/USD, GBP/USD, USD/JPY" },
  { id: 2, assetClass: "US Treasuries / Yields", beat: "Up Yields", miss: "Down Yields", watch: "[2Y most sensitive to policy expectations; 10Y to growth/inflation path]", tickers: "US02Y, US10Y, TLT" },
  { id: 3, assetClass: "US Equities", beat: "Down Initially", miss: "Up Initially", watch: "[Strong data can hit equities if it removes cut expectations. Weak data can rally equities on cut hopes]", tickers: "SPX, NDX, RTY" },
  { id: 4, assetClass: "Gold (XAU/USD)", beat: "Down Moderate", miss: "Up Strong", watch: "[Gold is inversely correlated to real yields and USD. Watch safe-haven demand in extreme misses]", tickers: "XAU/USD, GDX" },
  { id: 5, assetClass: "Crude Oil", beat: "Up Mild", miss: "Down Mild", watch: "[Demand-side effect: strong growth supports oil, but USD inverse correlation also matters]", tickers: "WTI, Brent, XLE" },
];

function getWeekDates() {
  const today = new Date();
  const day = today.getDay();
  const monday = new Date(today);
  monday.setDate(today.getDate() + ((day === 0 ? 1 : 8 - day) % 7 || 7));
  return Array.from({length: 5}, (_, i) => {
    const d = new Date(monday); d.setDate(monday.getDate() + i);
    return d.toLocaleDateString("en-GB", { day:"2-digit", month:"short" });
  });
}

function blankEvent(weekDates) {
  return { id: Date.now() + Math.random(), day: "Monday", date: weekDates[0], region: "US", event: "", forecast: "", prior: "", impact: "MED" };
}

function ImpactBadge({ level }) {
  const cls = level === "HIGH" ? "badge-high" : level === "MED" ? "badge-med" : "badge-low";
  const icon = level === "HIGH" ? "!" : level === "MED" ? "~" : ".";
  return <span className={`badge ${cls}`}>{icon} {level}</span>;
}

// -- Fetch calendar from the dashboard API ---------------------
async function fetchCalendarFromWeb(weekLabel, onStatus) {
  onStatus("Loading high-impact economic events from the dashboard for " + weekLabel + "...");
  const res = await fetch("/api/calendar?days_back=0&days_forward=7&importance=1&limit=120");
  if (!res.ok) throw new Error(`Calendar API error ${res.status}`);
  const data = await res.json();
  const countryFlags = {
    US: "US",
    EU: "EU",
    UK: "UK",
    JP: "JP",
    DE: "DE",
    CA: "CA",
    CH: "CH",
    AU: "AU",
    NZ: "NZ",
    CN: "CN",
  };
  return (data.data?.events || [])
    .filter((event) => {
      const day = new Date(event.released_at).getDay();
      return day >= 1 && day <= 5;
    })
    .map((event) => {
      const releasedAt = new Date(event.released_at);
      const importance = Number(event.importance || 3);
      return {
        id: event.release_id || Date.now() + Math.random(),
        day: releasedAt.toLocaleDateString("en-US", { weekday: "long" }),
        date: releasedAt.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }),
        region: countryFlags[event.country_code] || event.country_code || "US",
        event: event.display_name || "Economic release",
        forecast: event.estimate ?? "-",
        prior: event.previous ?? "-",
        impact: importance === 1 ? "HIGH" : importance === 2 ? "MED" : "LOW",
      };
    });
}

function cleanPdfText(text) {
  return String(text)
    .replace(/[\u{1F1E6}-\u{1F1FF}]/gu, "")
    .replace(/[--]/g, "-")
    .replace(/[–—]/g, "-")
    .replace(/[•]/g, "-")

    .replace(/\s+\n/g, "\n")
    .trim();
}

// -- Generate editable starter copy locally --------------------
async function generateNarrative(type, context, onChunk) {
  const highImpactEvents = String(context)
    .split("\n")
    .filter((line) => line.includes("[HIGH]"))
    .slice(0, 4);
  const primaryFocus = highImpactEvents[0] || String(context).split("\n")[0] || "the main macro releases";
  const drafts = {
    theme: `This week is likely to be shaped by ${primaryFocus}, with traders comparing incoming data against the prior trend and consensus expectations. The key teaching point is how markets separate a one-off surprise from evidence that inflation, growth, or labor momentum is changing. Use the calendar to frame which releases can shift rate expectations, risk sentiment, and currency leadership.`,
    risks: [
      "• Consensus may be stale if recent central-bank language has changed the market reaction function.",
      "• A large revision to prior data can matter as much as the headline print.",
      "• Cross-asset confirmation may be weak if FX, rates, and equities interpret the same release differently.",
      "• Liquidity around the release window can exaggerate the first move before the market settles.",
    ].join("\n"),
    event_overview: `${context} is a scheduled macro release that helps traders assess the current direction of the economy. The market usually compares the actual result with consensus and the previous print to judge whether momentum is improving, cooling, or staying stable.\n\nMarkets care because the data can influence rate expectations, real yields, risk appetite, and relative currency strength. The strongest reaction usually comes when the result changes the policy narrative rather than simply beating or missing by a small amount.\n\nFor teaching purposes, focus on the transmission channel: which part of the release affects inflation pressure, growth momentum, labor slack, or central-bank confidence. FX and rates are usually the cleanest first read, while equity and commodity reactions can depend more on the broader risk backdrop.`,
    scenarios: [
      "• Strong beat: watch whether markets price a firmer policy path or stronger growth impulse.",
      "• In-line result: the reaction may fade unless details or revisions change the story.",
      "• Clear miss: focus on whether the print challenges the prevailing macro narrative.",
      "• Mixed details: separate the headline from revisions, components, and the central-bank relevance.",
    ].join("\n"),
  };
  const text = drafts[type] || String(context);
  onChunk(text);
  return text;
}

// -- Main App --------------------------------------------------
function MacroDashboardApp() {
  const [page, setPage] = useState("weekly");
  const [weekDates, setWeekDates] = useState(getWeekDates());
  const [events, setEvents] = useState([]);
  const [fetchStatus, setFetchStatus] = useState({ state: "idle", msg: "Ready to fetch high-impact calendar data", sub: "" });
  const [weeklyForm, setWeeklyForm] = useState({ weekLabel: "", instructor: "", course: "", theme: "", risks: "" });
  const [eventForm, setEventForm] = useState({ date: "", eventName: "", region: "US", time: "", impact: "HIGH", instructor: "", course: "", overview: "", scenarios: "" });
  const [consensusRows, setConsensusRows] = useState(DEFAULT_CONSENSUS_ROWS);
  const [scenarioRows, setScenarioRows] = useState(DEFAULT_SCENARIO_ROWS);
  const [assetRows, setAssetRows] = useState(DEFAULT_ASSET_ROWS);
  const [aiOutput, setAiOutput] = useState("");
  const [aiLoading, setAiLoading] = useState(false);
  const [activeAI, setActiveAI] = useState(null);
  const outputRef = useRef(null);

  useEffect(() => { if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight; }, [aiOutput]);

  const weekLabel = weekDates.length ? `${weekDates[0]}–${weekDates[4]}` : "";

  // Fetch calendar
  async function handleFetch() {
    setFetchStatus({ state: "loading", msg: "Fetching high-impact economic calendar…", sub: "Loading high-impact releases for week of " + weekLabel });
    try {
      const data = await fetchCalendarFromWeb(weekLabel, msg => setFetchStatus({ state: "loading", msg, sub: "" }));
      setEvents(data.map(e => ({ ...e, id: Date.now() + Math.random() })));
      setFetchStatus({ state: "success", msg: `? Loaded ${data.length} high-impact events for week of ${weekLabel}`, sub: "Review, edit, or add lower-impact events manually before exporting" });
    } catch (e) {
      setFetchStatus({ state: "idle", msg: "Fetch failed — " + e.message, sub: "Try again or add events manually" });
    }
  }

  function addEvent() { setEvents(ev => [...ev, blankEvent(weekDates)]); }
  function removeEvent(id) { setEvents(ev => ev.filter(e => e.id !== id)); }
  function updateEvent(id, field, val) { setEvents(ev => ev.map(e => e.id === id ? { ...e, [field]: val } : e)); }
  function updateStructuredRow(setter, id, field, val) { setter(rows => rows.map(row => row.id === id ? { ...row, [field]: val } : row)); }
  function addStructuredRow(setter, template) { setter(rows => [...rows, { ...template, id: Date.now() + Math.random() }]); }
  function removeStructuredRow(setter, id) { setter(rows => rows.filter(row => row.id !== id)); }

  // AI assist
  async function handleAI(type, context) {
    setActiveAI(type); setAiLoading(true); setAiOutput("");
    try {
      await generateNarrative(type, context, txt => setAiOutput(txt));
    } catch(e) { setAiOutput("Error: " + e.message); }
    setAiLoading(false);
  }

  const eventsSummary = events.map(e => `${e.day} ${e.date}: ${e.event} (${e.region}, ${e.impact})`).join("; ");

  function weeklyReportText() {
    return `WEEKLY PREVIEW - Week of ${weeklyForm.weekLabel || weekLabel}
Instructor: ${weeklyForm.instructor || "[Your name]"}
Course: ${weeklyForm.course || "[Course name]"}

ECONOMIC CALENDAR (${events.length} events)
${events.map(e => `${e.day} ${e.date} | ${e.region} | ${e.event} | Forecast: ${e.forecast || "—"} | Prior: ${e.prior || "—"} | ${e.impact}`).join("\n")}

MACRO THEME
${weeklyForm.theme || "[Fill in your macro theme]"}

TAIL RISKS
${weeklyForm.risks || "[Fill in tail risks]"}`;
  }

  function eventReportText() {
    return `EVENT PREP BRIEF
Event: ${eventForm.eventName || "[Event name]"}
Date: ${eventForm.date || "[Date]"}  Time: ${eventForm.time || "[Time UTC]"}
Region: ${eventForm.region}  Impact: ${eventForm.impact}
Instructor: ${eventForm.instructor || "[Name]"}

EVENT OVERVIEW
${eventForm.overview || "[Fill in event overview]"}

CONSENSUS & KEY LEVELS
${consensusRows.map(row => `${row.metric} | Consensus: ${row.consensus} | Prior: ${row.prior} | Range: ${row.range} | Why: ${row.why}`).join("\n")}

SCENARIO MATRIX
${scenarioRows.map(row => `${row.outcome}: ${row.dataPrint} | Market: ${row.marketReaction} | Policy: ${row.policyImplication} | Probability: ${row.probability}`).join("\n")}

SCENARIO NOTES
${eventForm.scenarios || "[Fill in scenario summary notes]"}

ASSET CLASS SENSITIVITY
${assetRows.map(row => `${row.assetClass} | Beat: ${row.beat} | Miss: ${row.miss} | Watch: ${row.watch} | Tickers: ${row.tickers}`).join("\n")}`;
  }

  function escapeXml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&apos;");
  }

  function odtParagraph(text, style = "Body") {
    return `<text:p text:style-name="${style}">${escapeXml(text)}</text:p>`;
  }

  function odtBullet(text) {
    return `<text:list text:style-name="BulletList"><text:list-item>${odtParagraph(text.replace(/^[•\-]\s*/, ""))}</text:list-item></text:list>`;
  }

  function odtTitleBlock(mainTitle, subtitle, dateLine) {
    return `<table:table table:name="TitleBlock" table:style-name="TitleBlockTable"><table:table-column table:style-name="FullWidthCol"/><table:table-row><table:table-cell table:style-name="TitleBlockCell" office:value-type="string">${odtParagraph(mainTitle, "DocMainTitle")}${odtParagraph(subtitle, "DocSubtitle")}${dateLine ? odtParagraph(dateLine, "DocDateLine") : ""}</table:table-cell></table:table-row></table:table>`;
  }

  function odtSectionHeader(num, text) {
    const safeName = `SH${num.replace(/\W/g, "")}`;
    return `<table:table table:name="${safeName}" table:style-name="SectionHeaderTable"><table:table-column table:style-name="FullWidthCol"/><table:table-row><table:table-cell table:style-name="SectionHeaderCell" office:value-type="string"><text:p text:style-name="SectionHeaderText">${escapeXml(num)} — ${escapeXml(text)}</text:p></table:table-cell></table:table-row></table:table>`;
  }

  function odtDisclaimerBox(text) {
    return `<table:table table:name="DisclaimerBox" table:style-name="DisclaimerTable"><table:table-column table:style-name="FullWidthCol"/><table:table-row><table:table-cell table:style-name="DisclaimerCell" office:value-type="string">${odtParagraph(text, "DisclaimerText")}</table:table-cell></table:table-row></table:table>`;
  }

  function odtHeading2(text) {
    return odtParagraph(text, "Heading2");
  }

  function odtTable(name, headers, rows, greenColIndices = []) {
    const n = headers.length;
    const colStyle = n <= 2 ? "ColW2" : n === 3 ? "ColW3" : n === 4 ? "ColW4" : n === 5 ? "ColW5" : n === 6 ? "ColW6" : "ColW7";
    const colDefs = headers.map(() => `<table:table-column table:style-name="${colStyle}"/>`).join("");
    const headerCells = headers.map(h =>
      `<table:table-cell table:style-name="DataHeaderCell" office:value-type="string">${odtParagraph(h, "TableHeader")}</table:table-cell>`
    ).join("");
    const rowXml = rows.map((row, ri) => {
      const base = ri % 2 === 0 ? "DataCell" : "DataCellAlt";
      const cells = row.map((cell, ci) => {
        const cellStyle = greenColIndices.includes(ci) ? "DataCellGreen" : base;
        const pStyle = greenColIndices.includes(ci) ? "TableCellGreen" : "TableCell";
        return `<table:table-cell table:style-name="${cellStyle}" office:value-type="string">${odtParagraph(cell, pStyle)}</table:table-cell>`;
      }).join("");
      return `<table:table-row>${cells}</table:table-row>`;
    }).join("");
    return `<table:table table:name="${escapeXml(name)}" table:style-name="DataTable">${colDefs}<table:table-row>${headerCells}</table:table-row>${rowXml}</table:table>`;
  }

  function odtAllStyles() {
    return `
  <style:style style:name="DocMainTitle" style:family="paragraph">
    <style:paragraph-properties fo:text-align="center" fo:margin-bottom="3pt" fo:margin-top="2pt"/>
    <style:text-properties fo:font-size="22pt" fo:font-weight="bold" fo:color="#F5A623" fo:font-family="Times New Roman" fo:font-family-generic="roman"/>
  </style:style>
  <style:style style:name="DocSubtitle" style:family="paragraph">
    <style:paragraph-properties fo:text-align="center" fo:margin-bottom="3pt" fo:margin-top="0pt"/>
    <style:text-properties fo:font-size="14pt" fo:font-weight="bold" fo:color="#FFFFFF" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="DocDateLine" style:family="paragraph">
    <style:paragraph-properties fo:text-align="center" fo:margin-top="3pt" fo:margin-bottom="2pt"/>
    <style:text-properties fo:font-size="10pt" fo:color="#CCCCCC" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="SectionHeaderText" style:family="paragraph">
    <style:paragraph-properties fo:margin-left="8pt" fo:margin-top="4pt" fo:margin-bottom="4pt" fo:margin-right="4pt"/>
    <style:text-properties fo:font-size="12pt" fo:font-weight="bold" fo:color="#FFFFFF" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="DisclaimerText" style:family="paragraph">
    <style:paragraph-properties fo:text-align="center" fo:margin-left="6pt" fo:margin-right="6pt" fo:margin-top="3pt" fo:margin-bottom="3pt"/>
    <style:text-properties fo:font-size="9.5pt" fo:font-weight="bold" fo:color="#92400E" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="Body" style:family="paragraph">
    <style:paragraph-properties fo:margin-top="2pt" fo:margin-bottom="3pt"/>
    <style:text-properties fo:font-size="10pt" fo:color="#111827" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="BodySmall" style:family="paragraph">
    <style:paragraph-properties fo:margin-top="2pt" fo:margin-bottom="2pt"/>
    <style:text-properties fo:font-size="9pt" fo:font-style="italic" fo:color="#374151" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="Heading2" style:family="paragraph">
    <style:paragraph-properties fo:margin-top="8pt" fo:margin-bottom="4pt"/>
    <style:text-properties fo:font-size="11pt" fo:font-weight="bold" fo:color="#4A9FE0" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="TableHeader" style:family="paragraph">
    <style:paragraph-properties fo:margin-left="4pt" fo:margin-right="4pt" fo:margin-top="3pt" fo:margin-bottom="3pt"/>
    <style:text-properties fo:font-size="9pt" fo:font-weight="bold" fo:color="#FFFFFF" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="TableCell" style:family="paragraph">
    <style:paragraph-properties fo:margin-left="4pt" fo:margin-right="4pt" fo:margin-top="2pt" fo:margin-bottom="2pt"/>
    <style:text-properties fo:font-size="9pt" fo:color="#111827" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="TableCellGreen" style:family="paragraph">
    <style:paragraph-properties fo:margin-left="4pt" fo:margin-right="4pt" fo:margin-top="2pt" fo:margin-bottom="2pt"/>
    <style:text-properties fo:font-size="9pt" fo:color="#00C896" fo:font-weight="bold" fo:font-family="Arial" fo:font-family-generic="swiss"/>
  </style:style>
  <style:style style:name="TitleBlockTable" style:family="table">
    <style:table-properties style:width="17cm" table:align="margins" fo:margin-bottom="10pt"/>
  </style:style>
  <style:style style:name="SectionHeaderTable" style:family="table">
    <style:table-properties style:width="17cm" table:align="margins" fo:margin-top="12pt" fo:margin-bottom="6pt"/>
  </style:style>
  <style:style style:name="DisclaimerTable" style:family="table">
    <style:table-properties style:width="17cm" table:align="margins" fo:margin-top="8pt" fo:margin-bottom="10pt"/>
  </style:style>
  <style:style style:name="DataTable" style:family="table">
    <style:table-properties style:width="17cm" table:align="margins" fo:margin-top="6pt" fo:margin-bottom="6pt"/>
  </style:style>
  <style:style style:name="FullWidthCol" style:family="table-column">
    <style:table-column-properties style:column-width="17cm"/>
  </style:style>
  <style:style style:name="ColW2" style:family="table-column">
    <style:table-column-properties style:column-width="8.25cm"/>
  </style:style>
  <style:style style:name="ColW3" style:family="table-column">
    <style:table-column-properties style:column-width="5.5cm"/>
  </style:style>
  <style:style style:name="ColW4" style:family="table-column">
    <style:table-column-properties style:column-width="4.1cm"/>
  </style:style>
  <style:style style:name="ColW5" style:family="table-column">
    <style:table-column-properties style:column-width="3.2cm"/>
  </style:style>
  <style:style style:name="ColW6" style:family="table-column">
    <style:table-column-properties style:column-width="2.7cm"/>
  </style:style>
  <style:style style:name="ColW7" style:family="table-column">
    <style:table-column-properties style:column-width="2.35cm"/>
  </style:style>
  <style:style style:name="TitleBlockCell" style:family="table-cell">
    <style:table-cell-properties fo:background-color="#0D0F14" fo:padding="14pt" fo:border="none"/>
  </style:style>
  <style:style style:name="SectionHeaderCell" style:family="table-cell">
    <style:table-cell-properties fo:background-color="#0D0F14" fo:padding-left="10pt" fo:padding-top="6pt" fo:padding-bottom="6pt" fo:padding-right="8pt" fo:border="none"/>
  </style:style>
  <style:style style:name="DisclaimerCell" style:family="table-cell">
    <style:table-cell-properties fo:background-color="#FFFBEB" fo:border="1pt solid #F59E0B" fo:padding="8pt"/>
  </style:style>
  <style:style style:name="DataHeaderCell" style:family="table-cell">
    <style:table-cell-properties fo:background-color="#0D0F14" fo:border="0.5pt solid #3D4656" fo:padding-left="5pt" fo:padding-right="5pt" fo:padding-top="4pt" fo:padding-bottom="4pt"/>
  </style:style>
  <style:style style:name="DataCell" style:family="table-cell">
    <style:table-cell-properties fo:background-color="#FFFFFF" fo:border="0.5pt solid #C3CBD6" fo:padding-left="5pt" fo:padding-right="5pt" fo:padding-top="3pt" fo:padding-bottom="3pt"/>
  </style:style>
  <style:style style:name="DataCellAlt" style:family="table-cell">
    <style:table-cell-properties fo:background-color="#F9FAFB" fo:border="0.5pt solid #C3CBD6" fo:padding-left="5pt" fo:padding-right="5pt" fo:padding-top="3pt" fo:padding-bottom="3pt"/>
  </style:style>
  <style:style style:name="DataCellGreen" style:family="table-cell">
    <style:table-cell-properties fo:background-color="#FFFFFF" fo:border="0.5pt solid #C3CBD6" fo:padding-left="5pt" fo:padding-right="5pt" fo:padding-top="3pt" fo:padding-bottom="3pt"/>
  </style:style>
  <text:list-style style:name="BulletList">
    <text:list-level-style-bullet text:level="1" text:bullet-char="&#x2022;">
      <style:list-level-properties text:space-before="4pt" text:min-label-width="6pt" fo:margin-left="6pt"/>
    </text:list-level-style-bullet>
  </text:list-style>`;
  }

  function odtContent(sections) {
    return `<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
  xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
  office:version="1.2">
  <office:automatic-styles>${odtAllStyles()}</office:automatic-styles>
  <office:body>
    <office:text>
      ${sections.join("\n")}
    </office:text>
  </office:body>
</office:document-content>`;
  }

  function odtStylesFile(headerLeft, headerRight) {
    return `<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
  xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
  office:version="1.2">
  <office:styles>
    <style:default-style style:family="paragraph">
      <style:text-properties fo:font-family="Arial" fo:font-family-generic="swiss"/>
    </style:default-style>
  </office:styles>
  <office:automatic-styles>
    <style:page-layout style:name="PageLayout">
      <style:page-layout-properties fo:page-width="21cm" fo:page-height="29.7cm"
        fo:margin-top="1.8cm" fo:margin-bottom="1.8cm"
        fo:margin-left="2cm" fo:margin-right="2cm"/>
      <style:header-style>
        <style:header-footer-properties fo:min-height="0.8cm" fo:margin-bottom="0.3cm"/>
      </style:header-style>
      <style:footer-style>
        <style:header-footer-properties fo:min-height="0.7cm" fo:margin-top="0.3cm"/>
      </style:footer-style>
    </style:page-layout>
    <style:style style:name="HdrLeft" style:family="paragraph">
      <style:paragraph-properties fo:border-bottom="0.5pt solid #F5A623" fo:padding-bottom="3pt"/>
      <style:text-properties fo:font-size="8pt" fo:color="#374151" fo:font-weight="bold" fo:font-family="Arial"/>
    </style:style>
    <style:style style:name="HdrRight" style:family="paragraph">
      <style:paragraph-properties fo:text-align="right" fo:border-bottom="0.5pt solid #F5A623" fo:padding-bottom="3pt"/>
      <style:text-properties fo:font-size="8pt" fo:color="#374151" fo:font-family="Arial"/>
    </style:style>
    <style:style style:name="FtrCenter" style:family="paragraph">
      <style:paragraph-properties fo:text-align="center" fo:border-top="0.5pt solid #C3CBD6" fo:padding-top="3pt"/>
      <style:text-properties fo:font-size="8pt" fo:color="#9CA3AF" fo:font-family="Arial"/>
    </style:style>
  </office:automatic-styles>
  <office:master-styles>
    <style:master-page style:name="Standard" style:page-layout-name="PageLayout">
      <style:header>
        <text:p text:style-name="HdrLeft">${escapeXml(headerLeft)}<text:tab/><text:tab/><text:tab/><text:tab/><text:tab/>${escapeXml(headerRight)}</text:p>
      </style:header>
      <style:footer>
        <text:p text:style-name="FtrCenter">&#x26A0; FOR EDUCATIONAL PURPOSES ONLY — NOT FINANCIAL ADVICE<text:tab/><text:tab/>Page <text:page-number text:select-page="current">1</text:page-number></text:p>
      </style:footer>
    </style:master-page>
  </office:master-styles>
</office:document-styles>`;
  }

  async function exportOdt(sections, filename, headerLeft, headerRight) {
    const JSZip = window.JSZip;
    if (!JSZip) {
      alert("ODF export library is still loading. Try again in a moment.");
      return;
    }
    const zip = new JSZip();
    zip.file("mimetype", "application/vnd.oasis.opendocument.text", { compression: "STORE" });
    zip.file("META-INF/manifest.xml", `<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
  <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
  <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
  <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
  <manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>
</manifest:manifest>`);
    zip.file("styles.xml", odtStylesFile(headerLeft || "MACRO MARKET INTELLIGENCE", headerRight || ""));
    zip.file("meta.xml", `<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" office:version="1.2"><office:meta><meta:generator>Macro Dashboard Brief Builder</meta:generator></office:meta></office:document-meta>`);
    zip.file("content.xml", odtContent(sections));
    const blob = await zip.generateAsync({ type: "blob", mimeType: "application/vnd.oasis.opendocument.text" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportWeeklyOdt() {
    const instructor = weeklyForm.instructor || "[Instructor Name]";
    const course = weeklyForm.course || "[Course / Programme Name]";
    const activeWeek = weeklyForm.weekLabel || weekLabel;
    const eventRows = events.map(e => [
      e.day.slice(0, 3).toUpperCase(),
      e.date,
      e.region.replace(/[^\x00-\x7F]/g, "").trim(),
      e.event,
      e.forecast || "—",
      e.prior || "—",
      (e.impact === "HIGH" ? "? HIGH" : e.impact === "MED" ? "? MED" : "? LOW"),
    ]);
    const riskLines = (weeklyForm.risks || [
      "Geopolitical / Political: [any weekend developments, elections, policy announcements]",
      "Earnings: [any major corporate earnings releases that could move indices or sectors]",
      "Liquidity: [any public holidays in major markets that could thin liquidity]",
      "Data Revisions: [prior prints under revision that could change the narrative]",
      "Fed / ECB / BoE speakers: [scheduled speeches or interviews]",
    ].join("\n")).split("\n").filter(Boolean);
    const themeLines = String(weeklyForm.theme || "[Fill in your macro theme and narrative here — 3-5 sentences covering the dominant market narrative, key risk events, and what could shift sentiment this week.]").split("\n").filter(Boolean);
    return exportOdt(
      [
        odtTitleBlock(
          "MACRO MARKET INTELLIGENCE",
          "WEEKLY MARKET PREVIEW",
          `Week of ${activeWeek} | Prepared Sunday [DD Month YYYY]`
        ),
        odtDisclaimerBox("EDUCATIONAL USE ONLY — This document is not a trading signal or financial advice. It is prepared to help students understand macroeconomic data and potential market dynamics."),

        odtSectionHeader("01", "WEEKLY MACRO THEME & NARRATIVE"),
        odtParagraph("[Week's Defining Theme — e.g. 'Fed Policy Uncertainty Meets NFP Week']", "Heading2"),
        ...themeLines.map(line => odtParagraph(line)),
        odtParagraph("Key themes to watch:", "Heading2"),
        odtBullet("Growth vs. Inflation trade-off — where is the economy in the cycle?"),
        odtBullet("Central bank divergence — which banks are cutting vs. holding vs. hiking?"),
        odtBullet("Risk sentiment — risk-on (equities/commodities up, USD down) or risk-off?"),
        odtBullet("Geopolitical / political backdrop and any weekend headlines to be aware of"),

        odtSectionHeader("02", "ECONOMIC CALENDAR"),
        odtParagraph("All times shown in UTC. Impact rating reflects potential volatility, not direction. Events are illustrative — update each week with live consensus data.", "BodySmall"),
        odtTable("EconomicCalendar", ["Day", "Date", "Region", "Event", "Forecast", "Prior", "Impact"], eventRows, [4]),
        odtParagraph("? HIGH — major market mover | ? MED — moderate volatility potential | ? LOW — minor or localized impact", "BodySmall"),

        odtSectionHeader("03", "PRIOR WEEK MARKET SNAPSHOT"),
        odtParagraph("Update with Friday closing prices. DMA = Day Moving Average. Bias reflects technical posture only — not a trade recommendation.", "BodySmall"),
        odtTable("MarketSnapshot", ["Asset / Index", "Weekly Close", "Wk Change", "50-DMA", "200-DMA", "Bias"], [
          ["S&P 500 (SPX)",    "[X,XXX.XX]", "[+X.X%]", "[X,XXX]",  "[X,XXX]",  "? Bullish"],
          ["Nasdaq 100 (NDX)", "[X,XXX.XX]", "[-X.X%]", "[XX,XXX]", "[XX,XXX]", "? Bearish"],
          ["Dow Jones (DJIA)", "[XX,XXX.X]", "[+X.X%]", "[XX,XXX]", "[XX,XXX]", "? Bullish"],
          ["FTSE 100",         "[X,XXX.X]",  "[+X.X%]", "[X,XXX]",  "[X,XXX]",  "? Neutral"],
          ["DAX 40",           "[XX,XXX.X]", "[+X.X%]", "[XX,XXX]", "[XX,XXX]", "? Bullish"],
          ["Nikkei 225",       "[XX,XXX.X]", "[-X.X%]", "[XX,XXX]", "[XX,XXX]", "? Bearish"],
          ["EUR/USD",          "[X.XXXX]",   "[+X.X%]", "[X.XXXX]", "[X.XXXX]", "? Bullish"],
          ["GBP/USD",          "[X.XXXX]",   "[+X.X%]", "[X.XXXX]", "[X.XXXX]", "? Neutral"],
          ["USD/JPY",          "[XXX.XX]",   "[-X.X%]", "[XXX.XX]", "[XXX.XX]", "? Bearish"],
          ["Gold (XAU/USD)",   "[X,XXX.XX]", "[+X.X%]", "[X,XXX]",  "[X,XXX]",  "? Bullish"],
          ["WTI Crude Oil",    "[XX.XX]",    "[-X.X%]", "[XX.XX]",  "[XX.XX]",  "? Bearish"],
          ["US 10Y Yield",     "[X.XX%]",    "[+Xbp]",  "—",        "—",        "? Neutral"],
          ["VIX (Fear Index)", "[XX.XX]",    "[-X.X]",  "—",        "—",        "? Neutral"],
        ]),

        odtSectionHeader("04", "CENTRAL BANK POLICY TRACKER"),
        odtParagraph("Market pricing sourced from OIS/swap markets. Update rate decisions and meeting dates each week.", "BodySmall"),
        odtTable("CentralBanks", ["Central Bank", "Current Rate", "Last Decision", "Next Meeting", "Market Pricing", "Bias / Stance"], [
          ["Federal Reserve", "[X.XX–X.XX%]", "[Hold/Cut/Hike]", "[Month DD]", "[X% cut priced]",  "[Hawkish/Dovish/Neutral — key language]"],
          ["ECB",             "[X.XX%]",      "[Hold/Cut/Hike]", "[Month DD]", "[X% cut priced]",  "[Stance — key language]"],
          ["Bank of England", "[X.XX%]",      "[Hold/Cut/Hike]", "[Month DD]", "[X% cut priced]",  "[Stance — key language]"],
          ["Bank of Japan",   "[X.XX%]",      "[Hold/Cut/Hike]", "[Month DD]", "[X% hike priced]", "[Stance — key language]"],
          ["SNB",             "[X.XX%]",      "[Hold/Cut/Hike]", "[Month DD]", "[X% cut priced]",  "[Stance — key language]"],
          ["Bank of Canada",  "[X.XX%]",      "[Hold/Cut/Hike]", "[Month DD]", "[X% cut priced]",  "[Stance — key language]"],
        ]),

        odtSectionHeader("05", "ASSET CLASS & SECTOR OUTLOOK"),
        odtHeading2("Equities"),
        odtBullet("[Describe the key technical levels and fundamental drivers for global indices this week. What could push equities higher? What are the downside risks?]"),
        odtBullet("Support / Resistance: S&P 500 [X,XXX] / [X,XXX] — [instructor note on level significance]"),
        odtHeading2("Fixed Income / Yields"),
        odtBullet("[Describe yield dynamics — is the curve steepening or flattening? What is the bond market pricing for policy?]"),
        odtBullet("US 10Y focal level: [X.XX%] — watch for break above/below ahead of [event]"),
        odtHeading2("FX (Major Pairs)"),
        odtBullet("[Describe USD posture entering the week and key pair dynamics]"),
        odtBullet("EUR/USD — [key level / driver]. GBP/USD — [key level]. USD/JPY — [intervention risk / carry dynamics]"),
        odtHeading2("Commodities"),
        odtBullet("[Gold — safe haven demand, real yield dynamics]. [Oil — supply/demand, OPEC+ backdrop]. [Other commodities if relevant]"),

        odtSectionHeader("06", "MACRO LEARNING FOCUS THIS WEEK"),
        odtParagraph("Each week we highlight one macro concept relevant to the upcoming events. Understanding the framework helps you read market reactions without needing to predict the outcome."),
        odtParagraph("This week's concept: [e.g. 'Reading the Labour Market — Beyond the Headline Number']", "Heading2"),
        odtBullet("[Key point 1 — e.g. NFP is revised heavily; watch the 3-month average trend rather than a single print]"),
        odtBullet("[Key point 2 — e.g. Participation rate tells you about labour supply; a falling unemployment rate on low participation is not strong]"),
        odtBullet("[Key point 3 — e.g. Average Hourly Earnings is the inflation signal — wages drive services inflation which the Fed watches most]"),
        odtBullet("[Suggested further reading / video / chart to review before Friday]"),

        odtSectionHeader("07", "TAIL RISKS & WILDCARDS"),
        ...riskLines.map(line => odtBullet(line.replace(/^[•\-]\s*/, ""))),

        odtParagraph(""),
        odtParagraph(`Prepared by ${instructor} | ${course}`),
        odtParagraph("This is an educational document. It does not constitute financial advice or a recommendation to trade."),
      ],
      `weekly-market-preview-${activeWeek.replace(/\s+/g, "-").toLowerCase()}.odt`,
      "MACRO MARKET INTELLIGENCE | WEEKLY PREVIEW",
      `Week of ${activeWeek}`
    );
  }

  function exportEventOdt() {
    const instructor = eventForm.instructor || "[Instructor Name]";
    const course = eventForm.course || "[Course / Programme Name]";
    const scenarioNotes = (eventForm.scenarios || "").split("\n").filter(Boolean);
    const eventName = eventForm.eventName || "[Event Name]";
    const overviewLines = String(eventForm.overview || "[Fill in event overview — explain what this indicator measures in plain English, why it matters to markets, and which transmission channels link it to monetary policy.]").split("\n").filter(Boolean);
    const regionClean = (eventForm.region || "[Region]").replace(/[^\x00-\x7F]/g, "").trim();
    return exportOdt(
      [
        odtTitleBlock(
          "EVENT PREPARATION BRIEF",
          "MACRO MARKET INTELLIGENCE",
          `${eventName} — ${eventForm.date || "[DD Month YYYY]"}`
        ),
        odtDisclaimerBox("This brief is for educational purposes only. It is designed to help you understand what data to expect and how markets might react — it is NOT a trading signal or financial advice."),

        odtSectionHeader("01", "WHAT IS THIS DATA? — EVENT OVERVIEW"),
        odtTable("QuickRef", ["Release Date", "Release Time", "Region", "Frequency", "Market Impact", "Revision Risk"], [
          [eventForm.date || "[DD Month YYYY]", eventForm.time || "[HH:MM UTC]", regionClean, "[Monthly/Qtly]", eventForm.impact === "HIGH" ? "? HIGH" : eventForm.impact === "MED" ? "? MED" : "? LOW", "[Low/Med/High]"],
        ]),
        odtHeading2("Definition & Purpose"),
        ...overviewLines.map(line => odtParagraph(line)),
        odtHeading2("Why It Matters to Markets"),
        odtBullet("[Why do traders care about this number? Which transmission channels link it to monetary policy?]"),
        odtBullet("[Which assets are most sensitive to this release and why?]"),
        odtHeading2("Key Sub-Components to Watch"),
        odtBullet("[Component 1 — headline figure: net number / rate / index level]"),
        odtBullet("[Component 2 — inflation or wage signal within the report]"),
        odtBullet("[Component 3 — unemployment / activity / sentiment sub-index]"),
        odtBullet("[Component 4 — participation rate / revisions / breadth measure]"),
        odtBullet("[Component 5 — prior month revisions: often significant and can flip the narrative]"),

        odtSectionHeader("02", "THE NUMBERS — CONSENSUS & KEY LEVELS"),
        odtParagraph("* Forecasts from Bloomberg consensus / Reuters poll. Range of estimates reflects the spread of economist forecasts.", "BodySmall"),
        odtTable("ConsensusLevels", ["Metric", "Consensus / Forecast", "Prior Print", "Range of Estimates", "Why This Level Matters"], consensusRows.map(row => [
          row.metric,
          row.consensus,
          row.prior,
          row.range,
          row.why,
        ]), [1]),

        odtSectionHeader("03", "SCENARIO OUTCOME MATRIX — WHAT TO EXPECT"),
        odtParagraph("This matrix maps potential data outcomes to likely market reactions and policy implications. It is NOT a prediction — markets are complex and outcomes depend on context. Use this to build awareness, not certainty.", "BodySmall"),
        odtTable("ScenarioMatrix", ["Outcome", "Data Print", "Expected Market Reaction", "Monetary Policy Implication", "Probability*"], scenarioRows.map(row => [
          row.outcome,
          row.dataPrint,
          row.marketReaction,
          row.policyImplication,
          row.probability,
        ])),
        ...scenarioNotes.map(line => odtBullet(line.replace(/^[•\-]\s*/, ""))),
        odtParagraph("* Probability estimates are subjective and illustrative. Fill in based on current market positioning and analyst surveys.", "BodySmall"),

        odtSectionHeader("04", "ASSET CLASS SENSITIVITY GUIDE"),
        odtParagraph("How each major asset class is expected to respond. Sensitivity ratings assume a material beat or miss vs consensus.", "BodySmall"),
        odtTable("AssetSensitivity", ["Asset Class", "Beat Sensitivity", "Miss Sensitivity", "What to Watch", "Key Pairs / Tickers"], assetRows.map(row => [
          row.assetClass,
          row.beat,
          row.miss,
          row.watch,
          row.tickers,
        ])),

        odtSectionHeader("05", "MONETARY POLICY IMPLICATIONS"),
        odtHeading2("Current Policy Backdrop"),
        odtParagraph("Central Bank:  [e.g. Federal Reserve / ECB / BoE]"),
        odtParagraph("Current Rate:  [X.XX–X.XX%]"),
        odtParagraph("Last Decision:  [Hold / +25bp / -25bp] on [Date]"),
        odtParagraph("Next Meeting:  [Date] | Market pricing: [X% probability of cut/hold/hike]"),
        odtParagraph("Current Mandate Priority:  [e.g. Fighting inflation / Supporting growth / Dual mandate balance]"),
        odtHeading2("How This Data Feeds Into Policy"),
        odtBullet("[Explain the transmission mechanism — e.g. 'The Fed watches NFP as part of its employment mandate. A persistently strong labour market keeps wages elevated, which keeps services inflation sticky — the hardest component of CPI to bring down.']"),
        odtBullet("[What level of this data print would the central bank describe as: (a) too hot, (b) in their target range, (c) a concern?]"),
        odtBullet("[Are there any scheduled Fed/ECB/BoE speakers before or after this release? What have they said recently?]"),
        odtHeading2("Rate Path Sensitivity"),
        odtBullet("[How many cuts / hikes are currently priced for this year by OIS/swap markets?]"),
        odtBullet("[How might today's print shift that pricing? e.g. 'A print above +250k could remove the June cut entirely and push first cut expectations to September']"),
        odtBullet("[Watch OIS pricing and Fed Fund Futures post-release for real-time policy repricing]"),

        odtSectionHeader("06", "HISTORICAL DATA & MARKET REACTIONS"),
        odtParagraph("Use this section to track how markets have responded to prior releases of this indicator. Understanding historical reactions helps calibrate expectations — but remember, context changes.", "BodySmall"),
        odtTable("HistoricalReactions", ["Release", "Actual", "Forecast", "Surprise", "USD Rx", "Equity Rx", "Observation"], [
          ["[MMM YY]", "[+XXXk]", "[+XXXk]", "[+XX]", "[+X.X%]", "[+X.X%]", "[One-line instructor observation on why market reacted as it did]"],
          ["[MMM YY]", "[+XXXk]", "[+XXXk]", "[-XX]", "[-X.X%]", "[-X.X%]", "[Observation]"],
          ["[MMM YY]", "[+XXXk]", "[+XXXk]", "[+XX]", "[Flat]",   "[+X.X%]", "[Observation]"],
          ["[MMM YY]", "[+XXXk]", "[+XXXk]", "[-XX]", "[-X.X%]", "[-X.X%]", "[Observation — e.g. 'Weak print but equities rallied — rate cut optimism dominated over growth fear']"],
          ["[MMM YY]", "[+XXXk]", "[+XXXk]", "[+XX]", "[+X.X%]", "[Flat]",  "[Observation]"],
        ], [3]),
        odtParagraph("USD Rx = USD index reaction in first 30 mins. Equity Rx = S&P 500 reaction in first 30 mins. + = strengthened/rallied, – = weakened/fell.", "BodySmall"),

        odtSectionHeader("07", "PRE-RELEASE MARKET CONTEXT"),
        odtHeading2("Where Are Markets Positioned Entering This Release?"),
        odtBullet("[CFTC Commitment of Traders — net positioning in USD, gold, equities futures: are speculators long or short?]"),
        odtBullet("[Options market — is there a vol skew? Is the options market pricing more risk to the upside or downside?]"),
        odtBullet("[Recent price action — has the asset been rallying or selling off into the event? Markets often 'price in' expectations pre-release]"),
        odtHeading2("Related Data Released Since Last Print"),
        odtBullet("[List any relevant leading indicators released since last month that give clues about today's print — e.g. ADP payrolls, ISM employment sub-index, jobless claims trend, Challenger job cuts]"),
        odtBullet("[Do these lead indicators point to a beat, miss, or in-line print vs consensus?]"),
        odtHeading2("The 'Buy the Rumour, Sell the News' Consideration"),
        odtBullet("[Has the expected outcome already been priced in? If markets have moved significantly pre-release, the post-release reaction may be muted or even counter-intuitive]"),
        odtBullet("[An in-line print after a big rally into the event can trigger a 'sell the news' pullback even though the data was 'good']"),

        odtSectionHeader("08", "LEARNING FRAMEWORK — HOW TO ANALYSE THE RELEASE"),
        odtParagraph("Use this framework after the release to deepen your macro analysis skills. Work through these questions after the data drops:"),
        odtHeading2("Step 1 — Read the Print"),
        odtBullet("What was the actual headline number vs consensus? What was the surprise direction and magnitude?"),
        odtBullet("Were any prior period numbers revised? By how much? Did revisions change the trend?"),
        odtBullet("What did the sub-components say? Were they consistent with the headline or diverging?"),
        odtHeading2("Step 2 — Watch the Immediate Market Reaction (0–5 minutes)"),
        odtBullet("Which assets moved first and by how much? Does this match the scenario matrix?"),
        odtBullet("Did the initial move hold, reverse, or extend in the first 30 minutes?"),
        odtHeading2("Step 3 — Understand the Narrative (Day / Week After)"),
        odtBullet("How did central bank speakers frame the data? Did it change their guidance?"),
        odtBullet("Did rate pricing shift materially in OIS markets? By how many basis points?"),
        odtBullet("Did the data change your own view of the macro cycle? Growth accelerating, stable, or slowing?"),
        odtHeading2("Step 4 — Update Your Macro View"),
        odtBullet("How does this data point fit into the broader picture alongside CPI, retail sales, PMIs, and other recent releases?"),
        odtBullet("What is the 'weight of evidence' telling you about the direction of monetary policy?"),

        odtParagraph(""),
        odtParagraph(`Prepared by ${instructor} | ${course} | ${eventForm.date || "[Date]"}`),
        odtParagraph("This document is for educational use only. It is not financial advice. Past market reactions do not guarantee future outcomes."),
      ],
      `daily-event-prep-${eventName.replace(/\s+/g, "-").toLowerCase()}.odt`,
      "MACRO MARKET INTELLIGENCE | EVENT PREPARATION BRIEF",
      `${eventName} — ${eventForm.date || "[DD Month YYYY]"}`
    );
  }

  function exportPdf(title, text, filename) {
    const jsPDF = window.jspdf?.jsPDF;
    if (!jsPDF) {
      alert("PDF export library is still loading. Try again in a moment.");
      return;
    }
    const doc = new jsPDF({ unit: "pt", format: "a4" });
    const margin = 42;
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    let y = 52;

    doc.setFont("helvetica", "bold");
    doc.setFontSize(16);
    doc.text(cleanPdfText(title), margin, y);
    y += 28;

    doc.setFont("helvetica", "normal");
    doc.setFontSize(10);
    const lines = doc.splitTextToSize(cleanPdfText(text), pageWidth - margin * 2);
    lines.forEach((line) => {
      if (y > pageHeight - margin) {
        doc.addPage();
        y = margin;
      }
      doc.text(line, margin, y);
      y += 14;
    });
    doc.save(filename);
  }

  // Days with events
  const daysWithEvents = new Set(events.map(e => e.day));

  return (
    <>
      <style>{css}</style>
      <div className="app">
        {/* -- Sidebar -- */}
        <nav className="sidebar">
          <div className="sidebar-logo">
            <div className="sidebar-logo-text">Macro Market<br/>Intelligence</div>
            <div className="sidebar-logo-sub">Report Dashboard</div>
          </div>
          <div className="sidebar-nav">
            <div className="nav-section-label">Reports</div>
            {[
              { id: "weekly", label: "Weekly Preview", desc: "Sunday dispatch" },
              { id: "event", label: "Event Prep Brief", desc: "Daily event docs" },
            ].map(n => (
              <div key={n.id} className={`nav-item ${page === n.id ? "active" : ""}`} onClick={() => setPage(n.id)}>
                <div className="nav-dot" />
                <div>
                  <div>{n.label}</div>
                  <div style={{ fontSize: 10, opacity: 0.6, marginTop: 1 }}>{n.desc}</div>
                </div>
              </div>
            ))}
            <div className="nav-section-label">Dashboard</div>
            <a className="nav-item nav-link" href="/">
              <div className="nav-dot" />
              <div>
                <div>Main Dashboard</div>
                <div style={{ fontSize: 10, opacity: 0.6, marginTop: 1 }}>Macro monitor</div>
              </div>
            </a>
            <div className="nav-section-label" style={{ marginTop: 24 }}>How to Use</div>
            {[
              "1. Fetch the calendar",
              "2. Edit events if needed",
              "3. Generate starter text",
              "4. Export branded .odt",
            ].map((t, i) => (
              <div key={i} style={{ padding: "6px 12px", fontSize: 11, color: T.grey, lineHeight: 1.5 }}>{t}</div>
            ))}
          </div>
          <div style={{ padding: "16px 20px", borderTop: "1px solid rgba(255,255,255,0.06)", fontSize: 10, color: T.grey, lineHeight: 1.6 }}>
            Not financial advice.<br/>Educational use only.
          </div>
        </nav>

        {/* -- Main -- */}
        <main className="main">

          {/* -- WEEKLY PAGE -------------------------------------- */}
          {page === "weekly" && (
            <>
              <div className="page-header">
                <div className="page-title">Weekly <span>Preview</span></div>
                <div className="page-subtitle">Build your Sunday dispatch — fetch the calendar, draft your narrative, copy to Word</div>
              </div>

              {/* Step 1 — Week & fetch */}
              <div className="card">
                <div className="card-header">
                  <span className="card-number">01</span>
                  <div>
                    <div className="card-title">Week & Calendar Fetch</div>
                    <div className="card-desc">Auto-fetch economic events for the week ahead using live web search</div>
                  </div>
                </div>

                <div className="fetch-bar">
                  <div>
                    <label>Week of</label>
                    <input value={weeklyForm.weekLabel || weekLabel} onChange={e => setWeeklyForm(f => ({...f, weekLabel: e.target.value}))} placeholder={weekLabel} />
                  </div>
                  <div>
                    <label>Instructor Name</label>
                    <input value={weeklyForm.instructor} onChange={e => setWeeklyForm(f => ({...f, instructor: e.target.value}))} placeholder="Your name" />
                  </div>
                  <div>
                    <label>Course / Programme</label>
                    <input value={weeklyForm.course} onChange={e => setWeeklyForm(f => ({...f, course: e.target.value}))} placeholder="e.g. Macro Trading Fundamentals" />
                  </div>
                  <button className="btn btn-primary" onClick={handleFetch} disabled={fetchStatus.state === "loading"}>
                    {fetchStatus.state === "loading" ? "? Fetching…" : "? Fetch High Impact"}
                  </button>
                </div>

                <div className="status-bar">
                  <div className={`status-dot ${fetchStatus.state}`} />
                  <div>
                    <div className="status-text">{fetchStatus.msg}</div>
                    {fetchStatus.sub && <div className="status-sub">{fetchStatus.sub}</div>}
                  </div>
                </div>

                {/* Day strip */}
                <div className="week-strip">
                  {DAYS.map(d => (
                    <div key={d} className={`week-day ${daysWithEvents.has(d) ? "has-events" : ""}`}>
                      {d.slice(0,3)} {daysWithEvents.has(d) ? `· ${events.filter(e=>e.day===d).length}` : ""}
                    </div>
                  ))}
                </div>

                {/* Events table */}
                {events.length > 0 && (
                  <>
                    <div className="section-label">Economic Calendar — {events.length} events</div>
                    <div style={{ overflowX: "auto" }}>
                      <table className="event-table">
                        <thead>
                          <tr>
                            <th>Day</th><th>Date</th><th>Region</th><th style={{minWidth:200}}>Event</th>
                            <th>Forecast</th><th>Prior</th><th>Impact</th><th style={{width:40}}></th>
                          </tr>
                        </thead>
                        <tbody>
                          {events.map(e => (
                            <tr key={e.id}>
                              <td>
                                <select value={e.day} onChange={ev => updateEvent(e.id, "day", ev.target.value)}>
                                  {DAYS.map(d => <option key={d}>{d}</option>)}
                                </select>
                              </td>
                              <td><input value={e.date} onChange={ev => updateEvent(e.id, "date", ev.target.value)} placeholder="DD Mon" style={{width:80}} /></td>
                              <td>
                                <select value={e.region} onChange={ev => updateEvent(e.id, "region", ev.target.value)}>
                                  {REGIONS.map(r => <option key={r}>{r}</option>)}
                                </select>
                              </td>
                              <td><input value={e.event} onChange={ev => updateEvent(e.id, "event", ev.target.value)} placeholder="Event name" /></td>
                              <td><input value={e.forecast} onChange={ev => updateEvent(e.id, "forecast", ev.target.value)} placeholder="—" style={{width:80}} /></td>
                              <td><input value={e.prior} onChange={ev => updateEvent(e.id, "prior", ev.target.value)} placeholder="—" style={{width:80}} /></td>
                              <td>
                                <select value={e.impact} onChange={ev => updateEvent(e.id, "impact", ev.target.value)}>
                                  {IMPACTS.map(i => <option key={i}>{i}</option>)}
                                </select>
                              </td>
                              <td>
                                <button className="btn btn-danger btn-sm" onClick={() => removeEvent(e.id)}>?</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div style={{marginTop:12}}>
                      <button className="btn btn-ghost btn-sm" onClick={addEvent}>+ Add Event</button>
                    </div>
                  </>
                )}
                {events.length === 0 && (
                  <div style={{textAlign:"center", padding:"24px 0", color: T.grey}}>
                    <div style={{fontSize:28, marginBottom:8}}>PDF</div>
                    <div style={{fontSize:13}}>No events yet — fetch high-impact releases or add any event manually</div>
                    <button className="btn btn-ghost btn-sm" style={{marginTop:12}} onClick={addEvent}>+ Add Event Manually</button>
                  </div>
                )}
              </div>

              {/* Step 2 — Draft narrative */}
              <div className="card">
                <div className="card-header">
                  <span className="card-number">02</span>
                  <div>
                    <div className="card-title">Draft Narrative Sections</div>
                    <div className="card-desc">Generate starter text for your Weekly Theme and Tail Risks — edit freely before copying</div>
                  </div>
                </div>

                <div className="form-grid cols-2" style={{marginBottom:16}}>
                  <div>
                    <label>Weekly Macro Theme</label>
                    <textarea rows={4} value={weeklyForm.theme} onChange={e => setWeeklyForm(f=>({...f, theme: e.target.value}))}
                      placeholder="Describe the dominant macro narrative for this week…" />
                    <div style={{marginTop:8}}>
                      <button className="btn btn-ghost btn-sm" disabled={events.length===0 || aiLoading}
                        onClick={() => handleAI("theme", eventsSummary)}>
                        {activeAI==="theme" && aiLoading ? "? Writing…" : "? Draft Text"}
                      </button>
                      {activeAI==="theme" && aiOutput && !aiLoading && (
                        <button className="btn btn-ghost btn-sm" style={{marginLeft:6}}
                          onClick={() => setWeeklyForm(f=>({...f, theme: aiOutput}))}>? Use this</button>
                      )}
                    </div>
                  </div>
                  <div>
                    <label>Tail Risks & Wildcards</label>
                    <textarea rows={4} value={weeklyForm.risks} onChange={e => setWeeklyForm(f=>({...f, risks: e.target.value}))}
                      placeholder="Key risks to flag to students this week…" />
                    <div style={{marginTop:8}}>
                      <button className="btn btn-ghost btn-sm" disabled={events.length===0 || aiLoading}
                        onClick={() => handleAI("risks", eventsSummary)}>
                        {activeAI==="risks" && aiLoading ? "? Writing…" : "? Draft Text"}
                      </button>
                      {activeAI==="risks" && aiOutput && !aiLoading && (
                        <button className="btn btn-ghost btn-sm" style={{marginLeft:6}}
                          onClick={() => setWeeklyForm(f=>({...f, risks: aiOutput}))}>? Use this</button>
                      )}
                    </div>
                  </div>
                </div>

                {(aiLoading || aiOutput) && (
                  <>
                    <div className="section-label">Draft Output</div>
                    <div style={{position:"relative"}}>
                      {!aiLoading && aiOutput && (
                        <button className="copy-btn" onClick={() => navigator.clipboard?.writeText(aiOutput)}>Copy</button>
                      )}
                      <div className="output-box" ref={outputRef}>{aiOutput || "Generating…"}</div>
                    </div>
                  </>
                )}
              </div>

              {/* Step 3 — Export report summary */}
              <div className="card" style={{background:"rgba(74,159,224,0.05)", borderColor:"rgba(74,159,224,0.20)"}}>
                <div className="card-header">
                  <span className="card-number">03</span>
                  <div>
                    <div className="card-title">Export OpenDocument Report</div>
                    <div className="card-desc">Everything ready? Export a branded .odt file with the theme, calendar, risks, and report sections</div>
                  </div>
                </div>
                <div className="output-box" style={{maxHeight:280}}>
                  <span className="line-gold">-- WEEKLY PREVIEW — Week of {weeklyForm.weekLabel || weekLabel} --{"\n"}</span>
                  <span className="line-gold">Instructor: </span>{weeklyForm.instructor || "[Your name]"}{"\n"}
                  <span className="line-gold">Course: </span>{weeklyForm.course || "[Course name]"}{"\n\n"}
                  <span className="line-gold">-- ECONOMIC CALENDAR ({events.length} events) --{"\n"}</span>
                  {events.map(e => `${e.day.slice(0,3).toUpperCase()}  ${e.date}  ${e.region}  ${e.event}  Forecast: ${e.forecast}  Prior: ${e.prior}  [${e.impact}]\n`).join("")}
                  {"\n"}<span className="line-gold">-- MACRO THEME --{"\n"}</span>
                  {weeklyForm.theme || "[Fill in your macro theme]"}{"\n\n"}
                  <span className="line-gold">-- TAIL RISKS --{"\n"}</span>
                  {weeklyForm.risks || "[Fill in tail risks]"}
                </div>
                <div style={{marginTop:12}}>
                  <button className="btn btn-primary" onClick={() => {
                    navigator.clipboard?.writeText(weeklyReportText());
                  }}>
                    Copy All to Clipboard
                  </button>
                  <button className="btn btn-primary" style={{marginLeft:10}} onClick={exportWeeklyOdt}>
                    Export ODT
                  </button>
                  <button className="btn btn-ghost" style={{marginLeft:10}} onClick={() => exportPdf("Weekly Preview", weeklyReportText(), "weekly-preview.pdf")}>
                    Export PDF
                  </button>
                </div>
              </div>
            </>
          )}

          {/* -- EVENT PREP PAGE ---------------------------------- */}
          {page === "event" && (
            <>
              <div className="page-header">
                <div className="page-title">Event <span>Prep Brief</span></div>
                <div className="page-subtitle">Build a same-day event preparation document for your students</div>
              </div>

              {/* Event details */}
              <div className="card">
                <div className="card-header">
                  <span className="card-number">01</span>
                  <div>
                    <div className="card-title">Event Details</div>
                    <div className="card-desc">Fill in the basics — or pick from this week's fetched calendar</div>
                  </div>
                </div>

                {events.length > 0 && (
                  <div style={{marginBottom:16}}>
                    <label>Quick-fill from this week's calendar</label>
                    <select onChange={ev => {
                      const e = events.find(e => e.event === ev.target.value);
                      if (e) setEventForm(f => ({...f, eventName: e.event, date: e.date, region: e.region, impact: e.impact}));
                    }} defaultValue="">
                      <option value="">— Select an event —</option>
                      {events.map(e => <option key={e.id} value={e.event}>{e.day} · {e.event} [{e.impact}]</option>)}
                    </select>
                  </div>
                )}

                <div className="form-grid cols-3" style={{marginBottom:14}}>
                  <div>
                    <label>Event Name</label>
                    <input value={eventForm.eventName} onChange={e => setEventForm(f=>({...f, eventName: e.target.value}))} placeholder="e.g. US Non-Farm Payrolls" />
                  </div>
                  <div>
                    <label>Release Date</label>
                    <input value={eventForm.date} onChange={e => setEventForm(f=>({...f, date: e.target.value}))} placeholder="DD Mon YYYY" />
                  </div>
                  <div>
                    <label>Release Time (UTC)</label>
                    <input value={eventForm.time} onChange={e => setEventForm(f=>({...f, time: e.target.value}))} placeholder="e.g. 12:30 UTC" />
                  </div>
                </div>
                <div className="form-grid cols-3">
                  <div>
                    <label>Region</label>
                    <select value={eventForm.region} onChange={e => setEventForm(f=>({...f, region: e.target.value}))}>
                      {REGIONS.map(r => <option key={r}>{r}</option>)}
                    </select>
                  </div>
                  <div>
                    <label>Market Impact</label>
                    <select value={eventForm.impact} onChange={e => setEventForm(f=>({...f, impact: e.target.value}))}>
                      {IMPACTS.map(i => <option key={i}>{i}</option>)}
                    </select>
                  </div>
                  <div>
                    <label>Instructor Name</label>
                    <input value={eventForm.instructor} onChange={e => setEventForm(f=>({...f, instructor: e.target.value}))} placeholder="Your name" />
                  </div>
                </div>
              </div>

              {/* Draft content generation */}
              <div className="card">
                <div className="card-header">
                  <span className="card-number">02</span>
                  <div>
                    <div className="card-title">Draft Content Sections</div>
                    <div className="card-desc">Draft the event overview and scenario matrix with one click — then edit to your standard</div>
                  </div>
                </div>

                <div className="form-grid cols-2" style={{marginBottom:16}}>
                  <div>
                    <label>Event Overview (What is this data?)</label>
                    <textarea rows={5} value={eventForm.overview} onChange={e => setEventForm(f=>({...f, overview: e.target.value}))}
                      placeholder="Explain what this indicator measures and why it matters to markets…" />
                    <div style={{marginTop:8}}>
                      <button className="btn btn-ghost btn-sm" disabled={!eventForm.eventName || aiLoading}
                        onClick={() => handleAI("event_overview", eventForm.eventName + " " + eventForm.region)}>
                        {activeAI==="event_overview" && aiLoading ? "? Writing…" : "? Draft Text"}
                      </button>
                      {activeAI==="event_overview" && aiOutput && !aiLoading && (
                        <button className="btn btn-ghost btn-sm" style={{marginLeft:6}}
                          onClick={() => setEventForm(f=>({...f, overview: aiOutput}))}>? Use this</button>
                      )}
                    </div>
                  </div>
                  <div>
                    <label>Scenario Matrix Summary</label>
                    <textarea rows={5} value={eventForm.scenarios} onChange={e => setEventForm(f=>({...f, scenarios: e.target.value}))}
                      placeholder="Beat / in-line / miss scenarios and expected market reactions…" />
                    <div style={{marginTop:8}}>
                      <button className="btn btn-ghost btn-sm" disabled={!eventForm.eventName || aiLoading}
                        onClick={() => handleAI("scenarios", eventForm.eventName)}>
                        {activeAI==="scenarios" && aiLoading ? "? Writing…" : "? Draft Text"}
                      </button>
                      {activeAI==="scenarios" && aiOutput && !aiLoading && (
                        <button className="btn btn-ghost btn-sm" style={{marginLeft:6}}
                          onClick={() => setEventForm(f=>({...f, scenarios: aiOutput}))}>? Use this</button>
                      )}
                    </div>
                  </div>
                </div>

                {(aiLoading || aiOutput) && (
                  <>
                    <div className="section-label">Draft Output</div>
                    <div style={{position:"relative"}}>
                      {!aiLoading && aiOutput && (
                        <button className="copy-btn" onClick={() => navigator.clipboard?.writeText(aiOutput)}>Copy</button>
                      )}
                      <div className="output-box" ref={outputRef}>{aiOutput || "Generating…"}</div>
                    </div>
                  </>
                )}
              </div>

              {/* Consensus and key levels */}
              <div className="card">
                <div className="card-header">
                  <span className="card-number">03</span>
                  <div>
                    <div className="card-title">The Numbers — Consensus & Key Levels</div>
                    <div className="card-desc">Add the headline forecast, prior print, estimate range, and why each level matters</div>
                  </div>
                </div>
                <div style={{overflowX:"auto"}}>
                  <table className="brief-builder-table">
                    <thead>
                      <tr>
                        <th>Metric</th>
                        <th>Consensus / Forecast</th>
                        <th>Prior Print</th>
                        <th>Range of Estimates</th>
                        <th>Why This Level Matters</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {consensusRows.map(row => (
                        <tr key={row.id}>
                          <td className="wide-cell"><textarea rows={2} value={row.metric} onChange={e => updateStructuredRow(setConsensusRows, row.id, "metric", e.target.value)} /></td>
                          <td className="mini-cell"><input value={row.consensus} onChange={e => updateStructuredRow(setConsensusRows, row.id, "consensus", e.target.value)} /></td>
                          <td className="mini-cell"><input value={row.prior} onChange={e => updateStructuredRow(setConsensusRows, row.id, "prior", e.target.value)} /></td>
                          <td className="mini-cell"><input value={row.range} onChange={e => updateStructuredRow(setConsensusRows, row.id, "range", e.target.value)} /></td>
                          <td className="wide-cell"><textarea rows={2} value={row.why} onChange={e => updateStructuredRow(setConsensusRows, row.id, "why", e.target.value)} /></td>
                          <td><button className="btn btn-danger btn-sm" onClick={() => removeStructuredRow(setConsensusRows, row.id)}>Remove</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="brief-table-actions">
                  <button className="btn btn-ghost btn-sm" onClick={() => addStructuredRow(setConsensusRows, { metric: "[New metric]", consensus: "[X]", prior: "[X]", range: "[X-Y]", why: "[Why this level matters]" })}>Add Row</button>
                </div>
              </div>

              {/* Scenario outcome matrix */}
              <div className="card">
                <div className="card-header">
                  <span className="card-number">04</span>
                  <div>
                    <div className="card-title">Scenario Outcome Matrix — What to Expect</div>
                    <div className="card-desc">Map each data outcome to likely market reaction, policy implication, and probability</div>
                  </div>
                </div>
                <div style={{overflowX:"auto"}}>
                  <table className="brief-builder-table">
                    <thead>
                      <tr>
                        <th>Outcome</th>
                        <th>Data Print</th>
                        <th>Expected Market Reaction</th>
                        <th>Monetary Policy Implication</th>
                        <th>Probability*</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {scenarioRows.map(row => (
                        <tr key={row.id}>
                          <td className="mini-cell"><input value={row.outcome} onChange={e => updateStructuredRow(setScenarioRows, row.id, "outcome", e.target.value)} /></td>
                          <td className="wide-cell"><textarea rows={3} value={row.dataPrint} onChange={e => updateStructuredRow(setScenarioRows, row.id, "dataPrint", e.target.value)} /></td>
                          <td className="wide-cell"><textarea rows={3} value={row.marketReaction} onChange={e => updateStructuredRow(setScenarioRows, row.id, "marketReaction", e.target.value)} /></td>
                          <td className="wide-cell"><textarea rows={3} value={row.policyImplication} onChange={e => updateStructuredRow(setScenarioRows, row.id, "policyImplication", e.target.value)} /></td>
                          <td className="mini-cell"><input value={row.probability} onChange={e => updateStructuredRow(setScenarioRows, row.id, "probability", e.target.value)} /></td>
                          <td><button className="btn btn-danger btn-sm" onClick={() => removeStructuredRow(setScenarioRows, row.id)}>Remove</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="brief-table-actions">
                  <button className="btn btn-ghost btn-sm" onClick={() => addStructuredRow(setScenarioRows, { outcome: "[Outcome]", dataPrint: "[Data print]", marketReaction: "[Market reaction]", policyImplication: "[Policy implication]", probability: "[X%]" })}>Add Row</button>
                </div>
              </div>

              {/* Asset sensitivity */}
              <div className="card">
                <div className="card-header">
                  <span className="card-number">05</span>
                  <div>
                    <div className="card-title">Asset Class Sensitivity Guide</div>
                    <div className="card-desc">Define beat/miss sensitivity and the key markets students should watch</div>
                  </div>
                </div>
                <div style={{overflowX:"auto"}}>
                  <table className="brief-builder-table">
                    <thead>
                      <tr>
                        <th>Asset Class</th>
                        <th>Beat Sensitivity</th>
                        <th>Miss Sensitivity</th>
                        <th>What to Watch</th>
                        <th>Key Pairs / Tickers</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {assetRows.map(row => (
                        <tr key={row.id}>
                          <td className="mini-cell"><textarea rows={2} value={row.assetClass} onChange={e => updateStructuredRow(setAssetRows, row.id, "assetClass", e.target.value)} /></td>
                          <td className="mini-cell"><input value={row.beat} onChange={e => updateStructuredRow(setAssetRows, row.id, "beat", e.target.value)} /></td>
                          <td className="mini-cell"><input value={row.miss} onChange={e => updateStructuredRow(setAssetRows, row.id, "miss", e.target.value)} /></td>
                          <td className="wide-cell"><textarea rows={3} value={row.watch} onChange={e => updateStructuredRow(setAssetRows, row.id, "watch", e.target.value)} /></td>
                          <td className="mini-cell"><textarea rows={2} value={row.tickers} onChange={e => updateStructuredRow(setAssetRows, row.id, "tickers", e.target.value)} /></td>
                          <td><button className="btn btn-danger btn-sm" onClick={() => removeStructuredRow(setAssetRows, row.id)}>Remove</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="brief-table-actions">
                  <button className="btn btn-ghost btn-sm" onClick={() => addStructuredRow(setAssetRows, { assetClass: "[Asset class]", beat: "[Beat]", miss: "[Miss]", watch: "[What to watch]", tickers: "[Tickers]" })}>Add Row</button>
                </div>
              </div>

              {/* Copy summary */}
              <div className="card" style={{background:"rgba(74,159,224,0.05)", borderColor:"rgba(74,159,224,0.20)"}}>
                <div className="card-header">
                  <span className="card-number">06</span>
                  <div>
                    <div className="card-title">Copy to Your Event Prep Template</div>
                    <div className="card-desc">Structured output ready to export as an OpenDocument .odt brief</div>
                  </div>
                </div>
                <div className="output-box" style={{maxHeight:260}}>
                  <span className="line-gold">-- EVENT PREP BRIEF --{"\n"}</span>
                  <span className="line-gold">Event: </span>{eventForm.eventName || "[Event name]"}{"\n"}
                  <span className="line-gold">Date: </span>{eventForm.date || "[Date]"}{"  "}<span className="line-gold">Time: </span>{eventForm.time || "[Time UTC]"}{"\n"}
                  <span className="line-gold">Region: </span>{eventForm.region}{"  "}<span className="line-gold">Impact: </span>{eventForm.impact}{"\n"}
                  <span className="line-gold">Instructor: </span>{eventForm.instructor || "[Name]"}{"\n\n"}
                  <span className="line-gold">-- EVENT OVERVIEW --{"\n"}</span>
                  {eventForm.overview || "[Fill in event overview]"}{"\n\n"}
                  <span className="line-gold">-- CONSENSUS & KEY LEVELS --{"\n"}</span>
                  {consensusRows.map(row => `${row.metric} | ${row.consensus} | Prior ${row.prior} | Range ${row.range}\n`).join("")}{"\n"}
                  <span className="line-gold">-- SCENARIO MATRIX --{"\n"}</span>
                  {scenarioRows.map(row => `${row.outcome}: ${row.dataPrint} | ${row.marketReaction}\n`).join("")}{"\n"}
                  <span className="line-gold">-- SCENARIO NOTES --{"\n"}</span>
                  {eventForm.scenarios || "[Fill in scenario summary notes]"}{"\n\n"}
                  <span className="line-gold">-- ASSET CLASS SENSITIVITY --{"\n"}</span>
                  {assetRows.map(row => `${row.assetClass}: Beat ${row.beat}; Miss ${row.miss}; Watch ${row.tickers}\n`).join("")}
                </div>
                <div style={{marginTop:12}}>
                  <button className="btn btn-primary" onClick={() => {
                    navigator.clipboard?.writeText(eventReportText());
                  }}>
                    Copy All to Clipboard
                  </button>
                  <button className="btn btn-primary" style={{marginLeft:10}} onClick={exportEventOdt}>
                    Export ODT
                  </button>
                  <button className="btn btn-ghost" style={{marginLeft:10}} onClick={() => exportPdf("Event Prep Brief", eventReportText(), "event-prep-brief.pdf")}>
                    Export PDF
                  </button>
                </div>
              </div>
            </>
          )}
        </main>
      </div>
    </>
  );
}


const root = ReactDOM.createRoot(document.getElementById('macro-dashboard-root'));
root.render(<MacroDashboardApp />);
