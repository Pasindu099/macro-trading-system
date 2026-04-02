from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# =========================================================
# PAGE CONFIG
# =========================================================
st.set_page_config(
    page_title="Country Profile",
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

    .page-header {
        background: linear-gradient(90deg, #8d1313 0%, #b02121 35%, #d04b1f 100%);
        border: 1px solid #ffb347;
        padding: 0.85rem 1rem;
        border-radius: 12px;
        margin-bottom: 1rem;
        box-shadow: 0 0 0 1px rgba(255,159,28,0.15), 0 8px 24px rgba(0,0,0,0.35);
    }

    .page-title {
        font-family: 'Orbitron', sans-serif;
        font-size: 1.7rem;
        color: #fff3e6;
        margin: 0;
        letter-spacing: 1px;
    }

    .page-subtitle {
        color: #ffe2c2;
        margin-top: 0.25rem;
        font-size: 0.96rem;
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

    .country-banner {
        background: linear-gradient(90deg, #1a1208 0%, #22150b 45%, #13161e 100%);
        border: 1px solid var(--border);
        border-left: 4px solid var(--accent);
        border-radius: 16px;
        padding: 0.9rem 1rem;
        margin-bottom: 1rem;
    }

    .country-name {
        font-family: 'Orbitron', sans-serif;
        font-size: 1.3rem;
        color: #ffd9a3;
        margin-bottom: 0.2rem;
    }

    .country-meta {
        color: var(--muted);
        font-size: 0.95rem;
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

    .footer-note {
        color: var(--muted);
        text-align: center;
        padding-top: 0.6rem;
        font-size: 0.9rem;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# =========================================================
# PATHS
# =========================================================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_FILE = PROJECT_ROOT / "data" / "staging" / "standardized_releases.csv"


# =========================================================
# HELPERS
# =========================================================
@st.cache_data
def load_data(file_path: Path) -> pd.DataFrame:
    df = pd.read_csv(file_path)
    df["release_date"] = pd.to_datetime(df["release_date"], errors="coerce")

    for col in ["actual", "forecast", "previous", "surprise"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def standardize_category_name(value: str) -> str:
    if pd.isna(value):
        return value

    value = str(value).strip()

    mapping = {
        "Employment": "Employment",
        "Employment Data": "Employment",
        "Economic Activity": "Economic Activity",
        "Inflation": "Inflation",
        "Inflation Data": "Inflation",
        "Inflation data": "Inflation",
        "Monetary Policy": "Monetary Policy",
        "Monetory Policy": "Monetary Policy",
    }

    return mapping.get(value, value)


def normalize_group(group: pd.DataFrame) -> pd.DataFrame:
    group = group.copy()
    series = group["actual"]
    std = series.std()

    if pd.isna(std) or std == 0:
        group["plot_value"] = series
    else:
        group["plot_value"] = (series - series.mean()) / std

    return group


def build_plotly_theme(fig: go.Figure, y_title: str) -> go.Figure:
    fig.update_layout(
        paper_bgcolor="#11131a",
        plot_bgcolor="#11131a",
        font=dict(color="#f3f4f6", family="Rajdhani"),
        title_font=dict(color="#ff9f1c", family="Orbitron", size=18),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(0,0,0,0)",
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0
        ),
        margin=dict(l=30, r=30, t=60, b=30),
        xaxis=dict(
            title="Date",
            gridcolor="rgba(255,255,255,0.08)",
            zerolinecolor="rgba(255,255,255,0.08)"
        ),
        yaxis=dict(
            title=y_title,
            gridcolor="rgba(255,255,255,0.08)",
            zerolinecolor="rgba(255,255,255,0.08)"
        ),
        hovermode="x unified"
    )
    return fig


# =========================================================
# HEADER
# =========================================================
st.markdown(
    """
    <div class="page-header">
        <div class="page-title">COUNTRY PROFILE</div>
        <div class="page-subtitle">
            Visualize macroeconomic data under Employment, Economic Activity, Inflation, and Monetary Policy
        </div>
    </div>
    """,
    unsafe_allow_html=True
)


# =========================================================
# LOAD DATA
# =========================================================
if not DATA_FILE.exists():
    st.error("standardized_releases.csv not found.")
    st.code(str(DATA_FILE))
    st.stop()

try:
    df = load_data(DATA_FILE)
except Exception as e:
    st.error("Failed to load dataset.")
    st.exception(e)
    st.stop()

if df.empty:
    st.error("The dataset is empty.")
    st.stop()

required_cols = ["country", "category", "indicator", "release_date", "actual"]
missing_cols = [col for col in required_cols if col not in df.columns]
if missing_cols:
    st.error(f"Missing required columns: {missing_cols}")
    st.stop()

df = df.copy()
df["country"] = df["country"].astype(str).str.strip()
df["category"] = df["category"].apply(standardize_category_name)
df["indicator"] = df["indicator"].astype(str).str.strip()

if "currency" not in df.columns:
    df["currency"] = pd.NA


# =========================================================
# SIDEBAR
# =========================================================
st.sidebar.markdown("## Country Profile Controls")

countries = sorted([c for c in df["country"].dropna().unique().tolist() if c and c.lower() != "nan"])
selected_country = st.sidebar.selectbox("Select Country", countries)

country_df = df[df["country"] == selected_country].copy()

if country_df.empty:
    st.warning("No data found for the selected country.")
    st.stop()

country_currency = (
    country_df["currency"].dropna().iloc[0]
    if not country_df["currency"].dropna().empty
    else "N/A"
)

allowed_categories = [
    "Employment",
    "Economic Activity",
    "Inflation",
    "Monetary Policy",
]

country_categories = [cat for cat in allowed_categories if cat in country_df["category"].dropna().unique().tolist()]

selected_categories = st.sidebar.multiselect(
    "Select Categories",
    country_categories,
    default=country_categories
)

filtered_df = country_df[country_df["category"].isin(selected_categories)].copy()

available_indicators = sorted(
    [i for i in filtered_df["indicator"].dropna().unique().tolist() if i and i.lower() != "nan"]
)

selected_indicators = st.sidebar.multiselect(
    "Select Indicators",
    available_indicators,
    default=available_indicators[: min(8, len(available_indicators))]
)

chart_mode = st.sidebar.radio(
    "Display Mode",
    ["Normalized", "Raw"],
    index=0
)

min_date = filtered_df["release_date"].min()
max_date = filtered_df["release_date"].max()

if pd.notna(min_date) and pd.notna(max_date):
    date_range = st.sidebar.date_input(
        "Date Range",
        value=(min_date.date(), max_date.date())
    )
else:
    date_range = None

show_correlation = st.sidebar.checkbox("Show Correlation Matrix", value=True)
show_latest_table = st.sidebar.checkbox("Show Latest Values Table", value=True)
show_raw_data = st.sidebar.checkbox("Show Raw Filtered Data", value=False)


# =========================================================
# FILTER DATA
# =========================================================
if not selected_categories:
    st.warning("Please select at least one category.")
    st.stop()

if not selected_indicators:
    st.warning("Please select at least one indicator.")
    st.stop()

plot_df = filtered_df[filtered_df["indicator"].isin(selected_indicators)].copy()

if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date = pd.to_datetime(date_range[0])
    end_date = pd.to_datetime(date_range[1])
    plot_df = plot_df[(plot_df["release_date"] >= start_date) & (plot_df["release_date"] <= end_date)]

plot_df = plot_df.sort_values("release_date")

if plot_df.empty:
    st.warning("No data available for the selected filters.")
    st.stop()


# =========================================================
# COUNTRY BANNER
# =========================================================
st.markdown(
    f"""
    <div class="country-banner">
        <div class="country-name">{selected_country} [{country_currency}]</div>
        <div class="country-meta">
            Categories loaded: {len(country_categories)} |
            Indicators loaded: {country_df["indicator"].nunique()} |
            Records: {len(country_df)}
        </div>
    </div>
    """,
    unsafe_allow_html=True
)


# =========================================================
# TOP METRICS
# =========================================================
m1, m2, m3, m4 = st.columns(4)
m1.metric("Indicators in View", plot_df["indicator"].nunique())
m2.metric("Categories in View", plot_df["category"].nunique())
m3.metric("Rows in View", len(plot_df))
m4.metric("Display Mode", chart_mode)


# =========================================================
# PREP CHART DATA
# =========================================================
if chart_mode == "Normalized":
    plot_df = plot_df.groupby("indicator", group_keys=False).apply(normalize_group)
    y_col = "plot_value"
    y_title = "Normalized value (z-score)"
else:
    plot_df["plot_value"] = plot_df["actual"]
    y_col = "plot_value"
    y_title = "Actual value"


# =========================================================
# MAIN COMBINED CHART
# =========================================================
st.markdown('<div class="panel-title">COMBINED COUNTRY VIEW</div>', unsafe_allow_html=True)

main_fig = px.line(
    plot_df,
    x="release_date",
    y=y_col,
    color="indicator",
    markers=True,
    title=f"{selected_country} — Combined Macro View"
)
main_fig = build_plotly_theme(main_fig, y_title)
st.plotly_chart(main_fig, use_container_width=True)


# =========================================================
# CATEGORY CHARTS
# =========================================================
st.markdown('<div class="panel-title">CATEGORY BREAKDOWN</div>', unsafe_allow_html=True)

for category in allowed_categories:
    category_df = plot_df[plot_df["category"] == category].copy()

    if category_df.empty:
        continue

    st.markdown('<div class="panel-box">', unsafe_allow_html=True)
    st.markdown(f'<div class="panel-title">{category}</div>', unsafe_allow_html=True)

    fig = px.line(
        category_df,
        x="release_date",
        y=y_col,
        color="indicator",
        markers=True,
        title=f"{selected_country} — {category}"
    )
    fig = build_plotly_theme(fig, y_title)
    st.plotly_chart(fig, use_container_width=True)

    st.markdown('</div>', unsafe_allow_html=True)


# =========================================================
# CORRELATION MATRIX
# =========================================================
if show_correlation:
    st.markdown('<div class="panel-title">CORRELATION MATRIX</div>', unsafe_allow_html=True)

    corr_source = (
        plot_df[["release_date", "indicator", "actual"]]
        .dropna()
        .pivot_table(index="release_date", columns="indicator", values="actual", aggfunc="last")
    )

    if corr_source.shape[1] >= 2 and len(corr_source) >= 2:
        corr_matrix = corr_source.corr()

        corr_fig = px.imshow(
            corr_matrix,
            text_auto=".2f",
            aspect="auto",
            color_continuous_scale="RdBu_r",
            title=f"{selected_country} — Indicator Correlation Matrix"
        )
        corr_fig.update_layout(
            paper_bgcolor="#11131a",
            plot_bgcolor="#11131a",
            font=dict(color="#f3f4f6", family="Rajdhani"),
            title_font=dict(color="#ff9f1c", family="Orbitron", size=18),
            margin=dict(l=30, r=30, t=60, b=30)
        )
        st.plotly_chart(corr_fig, use_container_width=True)
    else:
        st.info("Choose at least two indicators with overlapping data to calculate correlations.")


# =========================================================
# LATEST VALUES TABLE
# =========================================================
if show_latest_table:
    st.markdown('<div class="panel-title">LATEST VALUES</div>', unsafe_allow_html=True)

    latest_cols = [
        col for col in [
            "release_date", "category", "indicator", "actual", "forecast", "previous", "surprise"
        ] if col in plot_df.columns
    ]

    latest_df = (
        plot_df.sort_values("release_date")
        .groupby(["category", "indicator"], as_index=False)
        .tail(1)[latest_cols]
        .sort_values(["category", "indicator"])
        .reset_index(drop=True)
    )

    st.dataframe(latest_df, use_container_width=True)


# =========================================================
# RAW FILTERED DATA
# =========================================================
if show_raw_data:
    st.markdown('<div class="panel-title">RAW FILTERED DATA</div>', unsafe_allow_html=True)

    raw_cols = [
        col for col in [
            "release_date", "country", "currency", "category", "indicator",
            "actual", "forecast", "previous", "surprise"
        ] if col in plot_df.columns
    ]

    st.dataframe(
        plot_df[raw_cols].sort_values(["category", "indicator", "release_date"]),
        use_container_width=True
    )


# =========================================================
# FOOTER
# =========================================================
st.markdown(
    '<div class="footer-note">Country Profile — category-based macro visualization terminal</div>',
    unsafe_allow_html=True
)