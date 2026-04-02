import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))

import tempfile
from datetime import date
from pathlib import Path as FilePath

import pandas as pd
import streamlit as st
from sqlalchemy import text

from scripts.utils.db_connection import get_engine
from scripts.processing.transform_excel import transform_excel
from dashboard.components.theme import apply_theme


st.set_page_config(page_title="Economic Data Visualization", layout="wide")
apply_theme()

st.set_page_config(page_title="Data Management", layout="wide")
st.title("🛠️ Data Management")


@st.cache_data
def load_reference_data():
    engine = get_engine()

    countries = pd.read_sql(
        "SELECT country_id, country_name FROM countries ORDER BY country_name",
        engine,
    )

    categories = pd.read_sql(
        "SELECT category_id, category_name FROM categories ORDER BY category_name",
        engine,
    )

    indicators = pd.read_sql(
        """
        SELECT
            i.indicator_id,
            i.indicator_name,
            i.original_indicator_name,
            c.country_name,
            cat.category_name
        FROM indicators i
        JOIN countries c
            ON i.country_id = c.country_id
        JOIN categories cat
            ON i.category_id = cat.category_id
        ORDER BY c.country_name, cat.category_name, i.indicator_name
        """,
        engine,
    )

    return countries, categories, indicators


def get_country_id(conn, country_name: str) -> int:
    result = conn.execute(
        text("SELECT country_id FROM countries WHERE country_name = :name"),
        {"name": country_name},
    ).fetchone()

    if result is None:
        raise ValueError(f"Country not found: {country_name}")

    return result[0]


def get_category_id(conn, category_name: str) -> int:
    result = conn.execute(
        text("SELECT category_id FROM categories WHERE category_name = :name"),
        {"name": category_name},
    ).fetchone()

    if result is None:
        raise ValueError(f"Category not found: {category_name}")

    return result[0]


def get_or_create_indicator(
    conn,
    country_id: int,
    category_id: int,
    indicator_name: str,
    original_indicator_name: str,
) -> int:
    result = conn.execute(
        text(
            """
            SELECT indicator_id
            FROM indicators
            WHERE country_id = :country_id
              AND category_id = :category_id
              AND original_indicator_name = :original_indicator_name
            """
        ),
        {
            "country_id": country_id,
            "category_id": category_id,
            "original_indicator_name": original_indicator_name,
        },
    ).fetchone()

    if result:
        return result[0]

    result = conn.execute(
        text(
            """
            INSERT INTO indicators (
                country_id,
                category_id,
                indicator_name,
                original_indicator_name
            )
            VALUES (
                :country_id,
                :category_id,
                :indicator_name,
                :original_indicator_name
            )
            RETURNING indicator_id
            """
        ),
        {
            "country_id": country_id,
            "category_id": category_id,
            "indicator_name": indicator_name,
            "original_indicator_name": original_indicator_name,
        },
    ).fetchone()

    return result[0]


def to_python_null(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def upsert_observations(conn, df: pd.DataFrame, indicator_id: int) -> int:
    rows_written = 0

    for _, row in df.iterrows():
        if (
            pd.isna(row["period_date"])
            and pd.isna(row["release_date"])
            and pd.isna(row["actual_value"])
            and pd.isna(row["forecast_value"])
            and pd.isna(row["previous_value"])
        ):
            continue

        if pd.isna(row["period_date"]):
            continue

        conn.execute(
            text(
                """
                INSERT INTO observations (
                    indicator_id,
                    period_date,
                    release_date,
                    actual_value,
                    forecast_value,
                    previous_value
                )
                VALUES (
                    :indicator_id,
                    :period_date,
                    :release_date,
                    :actual_value,
                    :forecast_value,
                    :previous_value
                )
                ON CONFLICT (indicator_id, period_date, release_date)
                DO UPDATE SET
                    actual_value = EXCLUDED.actual_value,
                    forecast_value = EXCLUDED.forecast_value,
                    previous_value = EXCLUDED.previous_value
                """
            ),
            {
                "indicator_id": indicator_id,
                "period_date": to_python_null(row["period_date"]),
                "release_date": to_python_null(row["release_date"]),
                "actual_value": to_python_null(row["actual_value"]),
                "forecast_value": to_python_null(row["forecast_value"]),
                "previous_value": to_python_null(row["previous_value"]),
            },
        )
        rows_written += 1

    return rows_written


def parse_optional_float(value: str):
    value = str(value).strip()
    if value == "":
        return None
    return float(value)


@st.cache_data
def load_coverage_audit():
    engine = get_engine()
    query = """
    WITH expected AS (
        SELECT c.country_name, cat.category_name
        FROM countries c
        CROSS JOIN categories cat
    ),
    actual AS (
        SELECT DISTINCT country_name, category_name
        FROM macro_observations_view
    )
    SELECT
        e.country_name,
        e.category_name,
        CASE
            WHEN a.country_name IS NULL THEN 'Missing'
            ELSE 'Available'
        END AS status
    FROM expected e
    LEFT JOIN actual a
        ON e.country_name = a.country_name
       AND e.category_name = a.category_name
    ORDER BY e.country_name, e.category_name;
    """
    return pd.read_sql(query, engine)


@st.cache_data
def load_latest_updates():
    engine = get_engine()
    query = """
    SELECT
        country_name,
        category_name,
        indicator_name,
        MAX(period_date) AS latest_period_date,
        MAX(release_date) AS latest_release_date,
        COUNT(*) AS observation_count
    FROM macro_observations_view
    GROUP BY country_name, category_name, indicator_name
    ORDER BY country_name, category_name, indicator_name;
    """
    return pd.read_sql(query, engine)


countries_df, categories_df, indicators_df = load_reference_data()

tab1, tab2, tab3 = st.tabs(
    ["📂 Upload Excel", "✍️ Manual Entry", "📋 Coverage Audit"]
)

with tab1:
    st.subheader("Upload historical or newly downloaded Excel files")

    col1, col2 = st.columns(2)

    with col1:
        upload_country = st.selectbox(
            "Country",
            countries_df["country_name"].tolist(),
            key="upload_country",
        )

    with col2:
        upload_category = st.selectbox(
            "Category",
            categories_df["category_name"].tolist(),
            key="upload_category",
        )

    uploaded_files = st.file_uploader(
        "Upload one or more Excel files",
        type=["xlsx"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        st.info(
            "The uploaded files will be transformed using the same pipeline as your batch loader."
        )

    if st.button("Process Uploaded Files", type="primary"):
        if not uploaded_files:
            st.warning("Please upload at least one Excel file.")
        else:
            engine = get_engine()
            success_logs = []
            failure_logs = []

            for uploaded_file in uploaded_files:
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                        tmp.write(uploaded_file.getbuffer())
                        temp_path = FilePath(tmp.name)

                    transformed_df = transform_excel(temp_path)
                    raw_name = FilePath(uploaded_file.name).stem

                    with engine.begin() as conn:
                        country_id = get_country_id(conn, upload_country)
                        category_id = get_category_id(conn, upload_category)

                        indicator_id = get_or_create_indicator(
                            conn=conn,
                            country_id=country_id,
                            category_id=category_id,
                            indicator_name=raw_name,
                            original_indicator_name=raw_name,
                        )

                        rows_written = upsert_observations(
                            conn=conn,
                            df=transformed_df,
                            indicator_id=indicator_id,
                        )

                    success_logs.append(
                        {
                            "file_name": uploaded_file.name,
                            "indicator_name": raw_name,
                            "rows_written": rows_written,
                        }
                    )

                except Exception as e:
                    failure_logs.append(
                        {
                            "file_name": uploaded_file.name,
                            "error": str(e),
                        }
                    )

                finally:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            if success_logs:
                st.success(f"Successfully processed {len(success_logs)} file(s).")
                st.dataframe(pd.DataFrame(success_logs), use_container_width=True)

            if failure_logs:
                st.error(f"{len(failure_logs)} file(s) failed.")
                st.dataframe(pd.DataFrame(failure_logs), use_container_width=True)

            st.cache_data.clear()

with tab2:
    st.subheader("Manual single-release entry")

    manual_country = st.selectbox(
        "Country",
        countries_df["country_name"].tolist(),
        key="manual_country",
    )

    manual_category = st.selectbox(
        "Category",
        categories_df["category_name"].tolist(),
        key="manual_category",
    )

    filtered_indicators = indicators_df[
        (indicators_df["country_name"] == manual_country)
        & (indicators_df["category_name"] == manual_category)
    ].copy()

    indicator_options = ["Create New Indicator"] + sorted(
        filtered_indicators["indicator_name"].dropna().unique().tolist()
    )

    selected_indicator_option = st.selectbox(
        "Indicator",
        indicator_options,
        key="manual_indicator_select",
    )

    if selected_indicator_option == "Create New Indicator":
        manual_indicator_name = st.text_input("New indicator name")
        manual_original_indicator_name = st.text_input(
            "Original indicator name",
            value=manual_indicator_name,
        )
    else:
        manual_indicator_name = selected_indicator_option
        matched = filtered_indicators[
            filtered_indicators["indicator_name"] == selected_indicator_option
        ].iloc[0]
        manual_original_indicator_name = matched["original_indicator_name"]

        st.text_input(
            "Original indicator name",
            value=manual_original_indicator_name,
            disabled=True,
        )

    col1, col2 = st.columns(2)

    with col1:
        period_date = st.date_input(
            "Period date",
            value=date.today(),
            key="manual_period_date",
        )

    with col2:
        use_release_date = st.checkbox("Include release date", value=True)
        release_date = st.date_input(
            "Release date",
            value=date.today(),
            disabled=not use_release_date,
            key="manual_release_date",
        )

    col3, col4, col5 = st.columns(3)

    with col3:
        actual_value = st.text_input("Actual value", placeholder="e.g. 0.4")

    with col4:
        forecast_value = st.text_input("Forecast value", placeholder="optional")

    with col5:
        previous_value = st.text_input("Previous value", placeholder="optional")

    if st.button("Save Manual Entry", type="primary"):
        try:
            if not manual_indicator_name.strip():
                raise ValueError("Indicator name is required.")

            manual_df = pd.DataFrame(
                [
                    {
                        "period_date": pd.to_datetime(period_date),
                        "release_date": pd.to_datetime(release_date)
                        if use_release_date
                        else pd.NaT,
                        "actual_value": parse_optional_float(actual_value),
                        "forecast_value": parse_optional_float(forecast_value),
                        "previous_value": parse_optional_float(previous_value),
                    }
                ]
            )

            engine = get_engine()

            with engine.begin() as conn:
                country_id = get_country_id(conn, manual_country)
                category_id = get_category_id(conn, manual_category)

                indicator_id = get_or_create_indicator(
                    conn=conn,
                    country_id=country_id,
                    category_id=category_id,
                    indicator_name=manual_indicator_name.strip(),
                    original_indicator_name=manual_original_indicator_name.strip()
                    if manual_original_indicator_name
                    else manual_indicator_name.strip(),
                )

                upsert_observations(
                    conn=conn,
                    df=manual_df,
                    indicator_id=indicator_id,
                )

            st.success("Manual entry saved successfully.")
            st.dataframe(manual_df, use_container_width=True)
            st.cache_data.clear()

        except Exception as e:
            st.error(f"Failed to save manual entry: {e}")

with tab3:
    st.subheader("Coverage audit")
    coverage_df = load_coverage_audit()
    st.dataframe(coverage_df, use_container_width=True)

    st.subheader("Latest updates by indicator")
    latest_updates_df = load_latest_updates()
    st.dataframe(latest_updates_df, use_container_width=True)

    missing_only = coverage_df[coverage_df["status"] == "Missing"].copy()
    if not missing_only.empty:
        st.warning("Missing country-category combinations detected.")
        st.dataframe(missing_only, use_container_width=True)
    else:
        st.success("All country-category combinations are available.")