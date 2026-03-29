from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components


# =========================================================
# PAGE CONFIG
# =========================================================
st.set_page_config(
    page_title="MacroTerminal",
    layout="wide",
    initial_sidebar_state="expanded"
)


# =========================================================
# THEME
# =========================================================
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700&family=Rajdhani:wght@500;600;700&display=swap');

    :root {
        --bg: #070b12;
        --panel: #11131a;
        --panel-2: #171a22;
        --accent: #ff9f1c;
        --accent-2: #c63c2f;
        --text: #f3f4f6;
        --muted: #a1a1aa;
        --border: #2b2f38;
    }

    .stApp {
        background-color: var(--bg);
        color: var(--text);
        font-family: 'Rajdhani', sans-serif;
    }

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #131313 0%, #0d0f14 100%);
        border-right: 1px solid var(--border);
    }

    section[data-testid="stSidebar"] * {
        color: var(--text) !important;
        font-family: 'Rajdhani', sans-serif !important;
    }

    .block-container {
        padding-top: 1.1rem;
        padding-bottom: 1.5rem;
        max-width: 96%;
    }

    .terminal-header {
        background: linear-gradient(90deg, #8d1313 0%, #b02121 35%, #d04b1f 100%);
        border: 1px solid #ffb347;
        padding: 0.85rem 1rem;
        border-radius: 12px;
        margin-bottom: 1rem;
        box-shadow: 0 0 0 1px rgba(255,159,28,0.15), 0 8px 24px rgba(0,0,0,0.35);
    }

    .terminal-title {
        font-family: 'Orbitron', sans-serif;
        font-size: 1.8rem;
        color: #fff3e6;
        margin: 0;
        letter-spacing: 1px;
    }

    .terminal-subtitle {
        color: #ffe2c2;
        margin-top: 0.25rem;
        font-size: 0.98rem;
    }

    .nav-strip {
        display: flex;
        gap: 0.6rem;
        flex-wrap: wrap;
        margin-bottom: 1rem;
    }

    .nav-pill {
        background: #191919;
        border: 1px solid #6b1d1d;
        color: #ffd9a3;
        padding: 0.35rem 0.7rem;
        border-radius: 8px;
        font-weight: 700;
        letter-spacing: 0.4px;
    }

    .panel-box {
        background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
        border: 1px solid var(--border);
        border-left: 3px solid var(--accent);
        border-radius: 16px;
        padding: 0.95rem 1rem 0.9rem 1rem;
        box-shadow: 0 8px 24px rgba(0,0,0,0.28);
        margin-bottom: 1rem;
    }

    .panel-title {
        font-family: 'Orbitron', sans-serif;
        color: var(--accent);
        font-size: 1rem;
        margin-bottom: 0.6rem;
        letter-spacing: 0.5px;
    }

    .small-muted {
        color: var(--muted);
        font-size: 0.9rem;
    }

    .status-badge {
        display: inline-block;
        background: #141821;
        border: 1px solid var(--border);
        color: #ffd9a3;
        border-radius: 10px;
        padding: 0.3rem 0.55rem;
        margin-right: 0.45rem;
        margin-bottom: 0.35rem;
        font-size: 0.88rem;
        font-weight: 700;
    }

    .country-card {
        background: linear-gradient(180deg, #11131a 0%, #0d1016 100%);
        border: 1px solid var(--border);
        border-top: 3px solid var(--accent);
        border-radius: 18px;
        padding: 0.85rem 1rem;
        min-height: 135px;
    }

    .country-name {
        font-family: 'Orbitron', sans-serif;
        font-size: 1rem;
        color: #ffd9a3;
        margin-bottom: 0.2rem;
    }

    .country-score {
        font-family: 'Orbitron', sans-serif;
        font-size: 1.5rem;
        color: var(--text);
        margin: 0.25rem 0;
    }

    .footer-note {
        color: var(--muted);
        text-align: center;
        padding-top: 0.6rem;
        font-size: 0.9rem;
    }

    div[data-testid="stMetric"] {
        background: linear-gradient(180deg, #13161d 0%, #0f1117 100%);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 0.6rem;
    }

    div[data-testid="stMetric"] label {
        color: var(--muted) !important;
        font-weight: 600;
    }

    div[data-testid="stMetricValue"] {
        color: var(--text) !important;
        font-family: 'Orbitron', sans-serif;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# =========================================================
# PATHS
# =========================================================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "dashboard" / "data"
STAGING_FILE = PROJECT_ROOT / "data" / "staging" / "standardized_releases.csv"
STRENGTH_FILE = DATA_DIR / "currency_strength_summary.csv"

DATA_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# HELPERS
# =========================================================
@st.cache_data(ttl=3600)
def safe_read_csv(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def regime_label(score):
    if pd.isna(score):
        return "N/A"
    if score >= 70:
        return "STRONG"
    if score >= 55:
        return "FIRM"
    if score >= 45:
        return "NEUTRAL"
    if score >= 30:
        return "SOFT"
    return "WEAK"


def fallback_strength_from_staging(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "currency", "country", "overall_score",
                "inflation_score", "employment_score",
                "growth_score", "policy_score"
            ]
        )

    required_cols = {"country", "currency", "category", "indicator", "actual", "release_date"}
    if not required_cols.issubset(set(df.columns)):
        return pd.DataFrame(
            columns=[
                "currency", "country", "overall_score",
                "inflation_score", "employment_score",
                "growth_score", "policy_score"
            ]
        )

    working = df.copy()
    working["release_date"] = pd.to_datetime(working["release_date"], errors="coerce")
    working = working.dropna(subset=["country", "currency", "indicator", "actual", "release_date"])

    if working.empty:
        return pd.DataFrame(
            columns=[
                "currency", "country", "overall_score",
                "inflation_score", "employment_score",
                "growth_score", "policy_score"
            ]
        )

    def infer_bucket(indicator: str, category: str) -> str:
        name = str(indicator).lower()
        cat = str(category).lower()

        if "interest rate" in name or "mpc vote" in name or "policy" in cat:
            return "Policy"
        if any(x in name for x in ["cpi", "pce", "ppi", "price", "inflation", "ippi", "rmpi"]) or "inflation" in cat:
            return "Inflation"
        if any(x in name for x in ["employment", "payroll", "unemployment", "jobless", "wages", "earnings", "jolts", "participation", "claims"]) or "employment" in cat:
            return "Employment"
        return "Growth"

    def inverse_indicator(indicator: str) -> bool:
        name = str(indicator).lower()
        return any(
            x in name for x in
            ["unemployment rate", "jobless claims", "initial jobless claims", "continuing jobless claims", "vote cut"]
        )

    rows = []
    for (country, currency), g in working.groupby(["country", "currency"]):
        scores = {"Inflation": [], "Employment": [], "Growth": [], "Policy": []}

        for indicator, sub in g.groupby("indicator"):
            sub = sub.sort_values("release_date")
            actuals = sub["actual"].dropna()
            if actuals.empty:
                continue

            latest = actuals.iloc[-1]
            series = sub["actual"].dropna()

            min_val = series.min()
            max_val = series.max()

            if min_val == max_val:
                score = 50.0
            else:
                score = (latest - min_val) / (max_val - min_val) * 100

            if inverse_indicator(indicator):
                score = 100 - score

            bucket = infer_bucket(indicator, sub["category"].iloc[0] if "category" in sub.columns and not sub.empty else "")
            scores[bucket].append(round(float(score), 1))

        inf = round(sum(scores["Inflation"]) / len(scores["Inflation"]), 1) if scores["Inflation"] else None
        emp = round(sum(scores["Employment"]) / len(scores["Employment"]), 1) if scores["Employment"] else None
        gro = round(sum(scores["Growth"]) / len(scores["Growth"]), 1) if scores["Growth"] else None
        pol = round(sum(scores["Policy"]) / len(scores["Policy"]), 1) if scores["Policy"] else None

        valid = [x for x in [inf, emp, gro, pol] if x is not None]
        overall = round(sum(valid) / len(valid), 1) if valid else None

        rows.append(
            {
                "currency": currency,
                "country": country,
                "overall_score": overall,
                "inflation_score": inf,
                "employment_score": emp,
                "growth_score": gro,
                "policy_score": pol,
            }
        )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("overall_score", ascending=False, na_position="last").reset_index(drop=True)
    return out


@st.cache_data(ttl=3600)
def load_strength_data(local_strength_file: Path, staging_file: Path) -> pd.DataFrame:
    strength_df = safe_read_csv(local_strength_file)
    if not strength_df.empty:
        return strength_df

    staging_df = safe_read_csv(staging_file)
    return fallback_strength_from_staging(staging_df)


# =========================================================
# DATA LOAD
# =========================================================
strength_df = load_strength_data(STRENGTH_FILE, STAGING_FILE)


# =========================================================
# HTML EMBEDS
# =========================================================
economic_calendar_html = """
<iframe src="https://sslecal2.investing.com?ecoDayBackground=%23b3b3b3&columns=exc_flags,exc_currency,exc_importance,exc_actual,exc_forecast,exc_previous&features=datepicker,timezone&countries=25,32,6,37,72,22,17,39,14,10,35,43,56,36,110,11,26,12,4,5&calType=week&timeZone=8&lang=1"
width="100%" height="500" frameborder="0" allowtransparency="true" marginwidth="0" marginheight="0"></iframe>
<div style="font-family: Arial, Helvetica, sans-serif; margin-top: 6px;">
<span style="font-size: 11px;color: #aaaaaa;">Real Time Economic Calendar provided by
<a href="https://www.investing.com/" rel="nofollow" target="_blank" style="font-size: 11px;color: #ff9f1c; font-weight: bold;">Investing.com</a>.
</span>
</div>
"""

interest_rates_html = """
<iframe frameborder="0" scrolling="no" height="90" width="100%" allowtransparency="true" marginwidth="0" marginheight="0"
src="https://sslirates.investing.com/index.php?rows=2&bg1=000000&bg2=ffa200&text_color=ffffff&enable_border=show&border_color=757575&header_bg=e00000&header_text=FFFFFF&force_lang=1"
align="center"></iframe>
<div style="margin-top: 6px; font-size: 11px; color: #aaaaaa;">
Interest Rates powered by
<a href="https://www.investing.com/" rel="nofollow" target="_blank" style="color: #ff9f1c; font-weight: bold;">Investing.com</a>.
</div>
"""

exchange_rates_html = """
<iframe frameborder="0" scrolling="auto" height="220" width="100%" allowtransparency="true" marginwidth="0" marginheight="0"
src="https://sslfxrates.investing.com/index_exchange.php?params&inner-border-color=%23000000&border-color=%23000000&bg1=%23ffaa00&bg2=%23ff0000&inner-text-color=%23050505&currency-name-color=%23000000&header-text-color=%23000000&force_lang=1"
align="center"></iframe>
<div style="margin-top: 6px; font-size: 11px; color: #aaaaaa;">
Exchange Rates powered by
<a href="https://www.investing.com/" rel="nofollow" target="_blank" style="color: #ff9f1c; font-weight: bold;">Investing.com</a>.
</div>
"""

economic_news_html = """
<script type="text/javascript">
DukascopyApplet = {
    "type":"online_news",
    "params":{
        "header":false,
        "borders":"#000000",
        "defaultLanguage":"en",
        "availableLanguages":["en"],
        "newsCategories":["finance","forex","stocks","commodities"],
        "width":"100%",
        "height":"100%",
        "adv":"popup"
    }
};
</script>
<script type="text/javascript" src="https://freeserv-static.dukascopy.com/2.0/core.js"></script>
<div style="color:#aaaaaa;font-size:11px;margin-top:6px;">Economic news powered by Dukascopy.</div>
"""


# =========================================================
# SIDEBAR
# =========================================================
st.sidebar.markdown("## Landing Page Controls")
show_debug = st.sidebar.checkbox("Show Debug Info", value=False)
show_currency_strength = st.sidebar.checkbox("Show Currency Strength", value=True)
show_calendar = st.sidebar.checkbox("Show Economic Calendar", value=True)
show_interest_rates = st.sidebar.checkbox("Show Interest Rates", value=True)
show_exchange_rates = st.sidebar.checkbox("Show Exchange Rates", value=True)
show_news = st.sidebar.checkbox("Show Economic News", value=True)


# =========================================================
# HEADER
# =========================================================
st.markdown(
    """
    <div class="terminal-header">
        <div class="terminal-title">MACROTERMINAL</div>
        <div class="terminal-subtitle">
            Macro regime, event risk, interest-rate intelligence, and currency strength command center
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <div class="nav-strip">
        <div class="nav-pill">HOME</div>
        <div class="nav-pill">COUNTRY PROFILES</div>
        <div class="nav-pill">CALENDAR</div>
        <div class="nav-pill">NEWS</div>
        <div class="nav-pill">RATES</div>
        <div class="nav-pill">FX BOARD</div>
        <div class="nav-pill">STRENGTH METER</div>
    </div>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <div>
        <span class="status-badge">SYSTEM: ONLINE</span>
        <span class="status-badge">LANDING PAGE: ACTIVE</span>
        <span class="status-badge">THEME: RETRO TERMINAL</span>
        <span class="status-badge">MODE: MACRO DESK</span>
    </div>
    """,
    unsafe_allow_html=True
)


# =========================================================
# TOP METRICS
# =========================================================
strongest = "N/A"
weakest = "N/A"

if not strength_df.empty and "currency" in strength_df.columns and "overall_score" in strength_df.columns:
    temp = strength_df.dropna(subset=["overall_score"]).sort_values("overall_score", ascending=False)
    if not temp.empty:
        strongest = f"{temp.iloc[0]['currency']} ({temp.iloc[0]['overall_score']:.1f})"
        weakest = f"{temp.iloc[-1]['currency']} ({temp.iloc[-1]['overall_score']:.1f})"

m1, m2, m3 = st.columns(3)
m1.metric("Currencies tracked", 0 if strength_df.empty else strength_df["currency"].nunique())
m2.metric("Strongest", strongest)
m3.metric("Weakest", weakest)


# =========================================================
# ROW 1
# =========================================================
col1, col2 = st.columns([1.15, 1])

with col1:
    if show_currency_strength:
        st.markdown('<div class="panel-box">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title">CURRENCY STRENGTH SUMMARY</div>', unsafe_allow_html=True)

        if not strength_df.empty:
            display_cols = [
                col for col in [
                    "currency", "country", "overall_score",
                    "inflation_score", "employment_score", "growth_score", "policy_score"
                ] if col in strength_df.columns
            ]
            strength_view = strength_df[display_cols].copy()

            if "overall_score" in strength_view.columns:
                strength_view["regime"] = strength_view["overall_score"].apply(regime_label)
                strength_view = strength_view.sort_values("overall_score", ascending=False, na_position="last")

            st.dataframe(strength_view, use_container_width=True)
        else:
            st.info("No strength data available yet.")

        st.markdown('</div>', unsafe_allow_html=True)

with col2:
    if show_interest_rates:
        st.markdown('<div class="panel-box">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title">INTEREST RATES TABLE</div>', unsafe_allow_html=True)
        components.html(interest_rates_html, height=150, scrolling=False)
        st.markdown('</div>', unsafe_allow_html=True)


# =========================================================
# ROW 2
# =========================================================
col3, col4 = st.columns([1.15, 1])

with col3:
    if show_calendar:
        st.markdown('<div class="panel-box">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title">ECONOMIC CALENDAR</div>', unsafe_allow_html=True)
        components.html(economic_calendar_html, height=560, scrolling=False)
        st.markdown('</div>', unsafe_allow_html=True)

with col4:
    if show_news:
        st.markdown('<div class="panel-box">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title">ECONOMIC NEWS</div>', unsafe_allow_html=True)
        components.html(economic_news_html, height=560, scrolling=True)
        st.markdown('</div>', unsafe_allow_html=True)


# =========================================================
# ROW 3
# =========================================================
if show_exchange_rates:
    st.markdown('<div class="panel-box">', unsafe_allow_html=True)
    st.markdown('<div class="panel-title">EXCHANGE RATES TABLE</div>', unsafe_allow_html=True)
    components.html(exchange_rates_html, height=300, scrolling=False)
    st.markdown('</div>', unsafe_allow_html=True)


# =========================================================
# QUICK COUNTRY ACCESS
# =========================================================
st.markdown('<div class="panel-title">QUICK COUNTRY ACCESS</div>', unsafe_allow_html=True)

if not strength_df.empty and {"currency", "country"}.issubset(strength_df.columns):
    cards_df = strength_df.copy()
    card_count = min(4, len(cards_df)) if len(cards_df) > 0 else 1
    card_cols = st.columns(card_count)

    for idx, (_, row) in enumerate(cards_df.head(8).iterrows()):
        col = card_cols[idx % len(card_cols)]
        country = row["country"]
        currency = row["currency"]
        overall = row["overall_score"] if "overall_score" in row else None
        inf = row["inflation_score"] if "inflation_score" in row else None
        emp = row["employment_score"] if "employment_score" in row else None
        gro = row["growth_score"] if "growth_score" in row else None
        pol = row["policy_score"] if "policy_score" in row else None

        overall_text = "N/A" if pd.isna(overall) else f"{overall:.1f}"
        inf_text = "N/A" if pd.isna(inf) else f"{inf:.1f}"
        emp_text = "N/A" if pd.isna(emp) else f"{emp:.1f}"
        gro_text = "N/A" if pd.isna(gro) else f"{gro:.1f}"
        pol_text = "N/A" if pd.isna(pol) else f"{pol:.1f}"

        col.markdown(
            f"""
            <div class="country-card">
                <div class="country-name">{country} [{currency}]</div>
                <div class="country-score">{overall_text}</div>
                <div class="small-muted">Regime: {regime_label(overall)}</div>
                <div class="small-muted">Inflation: {inf_text}</div>
                <div class="small-muted">Employment: {emp_text}</div>
                <div class="small-muted">Growth: {gro_text}</div>
                <div class="small-muted">Policy: {pol_text}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
else:
    st.info("Country cards will appear once currency strength data is available.")


# =========================================================
# DEBUG
# =========================================================
if show_debug:
    with st.expander("Debug Data Preview", expanded=True):
        st.write("Project root:", str(PROJECT_ROOT))
        st.write("Dashboard data dir:", str(DATA_DIR))
        st.write("Strength rows:", len(strength_df))
        st.write("Strength columns:", list(strength_df.columns) if not strength_df.empty else [])


# =========================================================
# FOOTER
# =========================================================
st.markdown(
    '<div class="footer-note">MacroTerminal Landing Page — macro command center for policy, events, and currency intelligence</div>',
    unsafe_allow_html=True
)