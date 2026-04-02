import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scripts.utils.db_connection import get_engine
from dashboard.components.theme import apply_theme


st.set_page_config(page_title="Economic Data Visualization", layout="wide")
apply_theme()

st.markdown("<div class='premium-card'>", unsafe_allow_html=True)
st.markdown("<div class='hero-title'>📊 Economic Data Visualization</div>", unsafe_allow_html=True)
st.markdown(
    "<div class='hero-sub'>Explore macro indicators with multiple chart styles, time filters, and surprise analysis.</div>",
    unsafe_allow_html=True,
)
st.markdown("</div>", unsafe_allow_html=True)


@st.cache_data
def load_data():
    engine = get_engine()
    query = "SELECT * FROM macro_observations_view"
    df = pd.read_sql(query, engine)
    df["period_date"] = pd.to_datetime(df["period_date"], errors="coerce")
    df["release_date"] = pd.to_datetime(df["release_date"], errors="coerce")
    return df


df = load_data()

# -----------------------------
# SIDEBAR FILTERS
# -----------------------------
st.sidebar.markdown("## Filters")

country_list = sorted(df["country_name"].dropna().unique())
selected_country = st.sidebar.selectbox("Country", country_list)

country_df = df[df["country_name"] == selected_country].copy()

category_list = sorted(country_df["category_name"].dropna().unique())
selected_category = st.sidebar.selectbox("Category", category_list)

filtered_df = country_df[country_df["category_name"] == selected_category].copy()

indicator_list = sorted(filtered_df["indicator_name"].dropna().unique())
selected_indicator = st.sidebar.selectbox("Indicator", indicator_list)

time_range = st.sidebar.selectbox(
    "Time Range",
    ["1Y", "2Y", "5Y", "10Y", "All"],
    index=4
)

chart_type = st.sidebar.selectbox(
    "Chart Type",
    ["Line", "Bar", "Area", "Scatter", "Histogram", "Box Plot"],
    index=0
)

st.sidebar.markdown("### Visible Series")
visible_series = st.sidebar.multiselect(
    "Choose series",
    ["Actual", "Forecast", "Previous"],
    default=["Actual", "Forecast", "Previous"]
)

chart_df = filtered_df[
    filtered_df["indicator_name"] == selected_indicator
].copy()

chart_df = chart_df.sort_values("period_date")


# -----------------------------
# TIME FILTER
# -----------------------------
if not chart_df.empty and time_range != "All":
    latest_date = chart_df["period_date"].max()

    if time_range == "1Y":
        cutoff_date = latest_date - pd.DateOffset(years=1)
    elif time_range == "2Y":
        cutoff_date = latest_date - pd.DateOffset(years=2)
    elif time_range == "5Y":
        cutoff_date = latest_date - pd.DateOffset(years=5)
    elif time_range == "10Y":
        cutoff_date = latest_date - pd.DateOffset(years=10)
    else:
        cutoff_date = None

    if cutoff_date is not None:
        chart_df = chart_df[chart_df["period_date"] >= cutoff_date]


tabs = st.tabs(["📈 Visualization", "📋 Latest Data", "⚡ Surprise"])


def add_series_trace(fig, x, y, name, selected_chart_type):
    color_map = {
        "Actual": "#ffcc00",
        "Forecast": "#ffd966",
        "Previous": "#b38f00",
    }
    color = color_map.get(name, "#ffcc00")

    if selected_chart_type == "Line":
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines+markers",
                name=name,
                line=dict(color=color, width=2.5),
                marker=dict(size=6, color=color),
            )
        )

    elif selected_chart_type == "Bar":
        fig.add_trace(
            go.Bar(
                x=x,
                y=y,
                name=name,
                marker_color=color,
            )
        )

    elif selected_chart_type == "Area":
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines",
                fill="tozeroy",
                name=name,
                line=dict(color=color, width=2.5),
            )
        )

    elif selected_chart_type == "Scatter":
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="markers",
                name=name,
                marker=dict(size=9, color=color),
            )
        )


def apply_yellow_black_layout(fig, title, height=560):
    fig.update_layout(
        template="plotly_dark",
        title=title,
        height=height,
        hovermode="x unified",
        margin=dict(l=20, r=20, t=60, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0b0b0b",
        font=dict(color="#ffcc00"),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(color="#ffcc00"),
        ),
    )

    fig.update_xaxes(
        title="Date",
        showgrid=True,
        gridcolor="rgba(255,204,0,0.15)",
        zeroline=False,
    )

    fig.update_yaxes(
        title="Value",
        showgrid=True,
        gridcolor="rgba(255,204,0,0.15)",
        zeroline=False,
    )


# -----------------------------
# TAB 1: MAIN VISUALIZATION
# -----------------------------
with tabs[0]:
    st.markdown("<div class='premium-card'>", unsafe_allow_html=True)

    left, right = st.columns([3, 1])

    with left:
        st.subheader(selected_indicator)

    with right:
        st.markdown(
            f"<p class='muted-text' style='text-align:right; margin-top:12px;'>"
            f"{selected_country} • {selected_category} • {time_range}</p>",
            unsafe_allow_html=True,
        )

    if chart_df.empty:
        st.warning("No data available for the selected filters.")
    else:
        fig = go.Figure()

        if chart_type in ["Line", "Bar", "Area", "Scatter"]:
            if "Actual" in visible_series:
                add_series_trace(
                    fig,
                    chart_df["period_date"],
                    chart_df["actual_value"],
                    "Actual",
                    chart_type
                )

            if "Forecast" in visible_series:
                add_series_trace(
                    fig,
                    chart_df["period_date"],
                    chart_df["forecast_value"],
                    "Forecast",
                    chart_type
                )

            if "Previous" in visible_series:
                add_series_trace(
                    fig,
                    chart_df["period_date"],
                    chart_df["previous_value"],
                    "Previous",
                    chart_type
                )

            apply_yellow_black_layout(fig, f"{chart_type} View")

        elif chart_type == "Histogram":
            if "Actual" in visible_series:
                fig.add_trace(
                    go.Histogram(
                        x=chart_df["actual_value"],
                        name="Actual",
                        marker_color="#ffcc00",
                        opacity=0.7,
                    )
                )

            if "Forecast" in visible_series:
                fig.add_trace(
                    go.Histogram(
                        x=chart_df["forecast_value"],
                        name="Forecast",
                        marker_color="#ffd966",
                        opacity=0.7,
                    )
                )

            if "Previous" in visible_series:
                fig.add_trace(
                    go.Histogram(
                        x=chart_df["previous_value"],
                        name="Previous",
                        marker_color="#b38f00",
                        opacity=0.7,
                    )
                )

            apply_yellow_black_layout(fig, "Histogram View")
            fig.update_layout(barmode="overlay")
            fig.update_xaxes(title="Value")
            fig.update_yaxes(title="Frequency")

        elif chart_type == "Box Plot":
            if "Actual" in visible_series:
                fig.add_trace(
                    go.Box(
                        y=chart_df["actual_value"],
                        name="Actual",
                        boxmean=True,
                        marker_color="#ffcc00",
                        line_color="#ffcc00",
                    )
                )

            if "Forecast" in visible_series:
                fig.add_trace(
                    go.Box(
                        y=chart_df["forecast_value"],
                        name="Forecast",
                        boxmean=True,
                        marker_color="#ffd966",
                        line_color="#ffd966",
                    )
                )

            if "Previous" in visible_series:
                fig.add_trace(
                    go.Box(
                        y=chart_df["previous_value"],
                        name="Previous",
                        boxmean=True,
                        marker_color="#b38f00",
                        line_color="#b38f00",
                    )
                )

            apply_yellow_black_layout(fig, "Box Plot View")
            fig.update_xaxes(showgrid=False)
            fig.update_yaxes(title="Value")

        st.plotly_chart(fig, use_container_width=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("Country", selected_country)
        c2.metric("Category", selected_category)
        c3.metric("Rows Displayed", len(chart_df))

        st.info(
            "Line/Area = trends • Bar = release comparisons • Scatter = point patterns • Histogram = distribution • Box Plot = spread/outliers"
        )

    st.markdown("</div>", unsafe_allow_html=True)

# -----------------------------
# TAB 2: LATEST DATA
# -----------------------------
with tabs[1]:
    st.markdown("<div class='premium-card'>", unsafe_allow_html=True)
    st.subheader("Latest Data Snapshot")

    latest_row = (
        filtered_df[filtered_df["indicator_name"] == selected_indicator]
        .sort_values("period_date", ascending=False)
        .head(1)
        .copy()
    )

    if latest_row.empty:
        st.warning("No latest data found.")
    else:
        actual_val = latest_row["actual_value"].iloc[0]
        forecast_val = latest_row["forecast_value"].iloc[0]
        previous_val = latest_row["previous_value"].iloc[0]

        col1, col2, col3 = st.columns(3)
        col1.metric("Actual", f"{actual_val}" if pd.notna(actual_val) else "N/A")
        col2.metric("Forecast", f"{forecast_val}" if pd.notna(forecast_val) else "N/A")
        col3.metric("Previous", f"{previous_val}" if pd.notna(previous_val) else "N/A")

        st.dataframe(
            latest_row[
                [
                    "indicator_name",
                    "period_date",
                    "release_date",
                    "actual_value",
                    "forecast_value",
                    "previous_value",
                ]
            ],
            use_container_width=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)

# -----------------------------
# TAB 3: SURPRISE
# -----------------------------
with tabs[2]:
    st.markdown("<div class='premium-card'>", unsafe_allow_html=True)
    st.subheader("Surprise Analysis")

    if chart_df.empty:
        st.warning("No data available for surprise analysis.")
    else:
        surprise_df = chart_df.copy()
        surprise_df["surprise"] = (
            surprise_df["actual_value"] - surprise_df["forecast_value"]
        )

        surprise_chart_type = st.selectbox(
            "Surprise Chart Type",
            ["Bar", "Line", "Area", "Scatter"],
            index=0
        )

        fig = go.Figure()

        if surprise_chart_type == "Bar":
            fig.add_trace(
                go.Bar(
                    x=surprise_df["period_date"],
                    y=surprise_df["surprise"],
                    name="Surprise",
                    marker_color="#ffcc00",
                )
            )
        elif surprise_chart_type == "Line":
            fig.add_trace(
                go.Scatter(
                    x=surprise_df["period_date"],
                    y=surprise_df["surprise"],
                    mode="lines+markers",
                    name="Surprise",
                    line=dict(color="#ffcc00", width=2.5),
                    marker=dict(size=6, color="#ffcc00"),
                )
            )
        elif surprise_chart_type == "Area":
            fig.add_trace(
                go.Scatter(
                    x=surprise_df["period_date"],
                    y=surprise_df["surprise"],
                    mode="lines",
                    fill="tozeroy",
                    name="Surprise",
                    line=dict(color="#ffcc00", width=2.5),
                )
            )
        elif surprise_chart_type == "Scatter":
            fig.add_trace(
                go.Scatter(
                    x=surprise_df["period_date"],
                    y=surprise_df["surprise"],
                    mode="markers",
                    name="Surprise",
                    marker=dict(size=9, color="#ffcc00"),
                )
            )

        apply_yellow_black_layout(fig, f"Actual - Forecast ({time_range})", height=500)
        fig.update_yaxes(title="Surprise")

        st.plotly_chart(fig, use_container_width=True)

        latest_surprise = surprise_df.sort_values("period_date", ascending=False).head(1)
        if not latest_surprise.empty:
            surprise_val = latest_surprise["surprise"].iloc[0]
            st.metric(
                "Latest Surprise",
                f"{round(surprise_val, 4)}" if pd.notna(surprise_val) else "N/A"
            )

    st.markdown("</div>", unsafe_allow_html=True)