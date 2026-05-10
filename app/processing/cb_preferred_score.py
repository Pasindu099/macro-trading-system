"""CB preferred-indicator strength score.

Each central bank publicly focuses on specific economic measures.
This module builds a currency strength score using only those CB-watched
indicators — bypassing the correlation filter used in macro_indices.py,
which loses too many indicators on only 5 years of data.

Scoring logic per currency
--------------------------
1. For each CB-preferred indicator, take the latest released signal as of
   the score date (within a 90-day staleness window).

   Signal priority (best available):
     standardized_surprise_score → expanding_z_score → momentum → mom_change

2. Multiply by direction (+1 = higher is hawkish, -1 = higher is dovish)
   and by the indicator's within-theme weight.

3. Average across indicators to produce three theme scores:
   inflation_score, labor_score, growth_score  (each approx [-1, 1])

4. Combine theme scores using CB mandate weights (same as cb_reaction_score):
   cb_pressure = (infl_w × infl + labor_w × labor + growth_w × growth) / total_w

5. Cross-currency z-score per date to produce cb_strength_score
   (relative strength vs all 8 peers on the same day).

CB watchlist sources
--------------------
  Fed   : PCE, Core PCE, NFP, Unemployment, AHE, ISM, Retail Sales
  ECB   : Core HICP, Headline HICP, Unemployment, GDP, Composite PMI
  BoE   : CPI, Core CPI, Avg Earnings (ex bonus), Unemployment, GDP, Services PMI
  BoJ   : Core CPI (ex fresh food), Tokyo Core CPI, Unemployment, GDP, Composite PMI
  RBA   : Trimmed Mean CPI, Monthly CPI, Employment, Unemployment, Composite PMI
  RBNZ  : CPI, Employment, Labour Cost Index, Unemployment
  BoC   : CPI-Trim, CPI-Median, CPI-Common, Employment, Unemployment, GDP MoM
  SNB   : CPI, Producer & Import Prices, Unemployment, GDP
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.processing.macro_dataset import csv_value, json_default


# ── CB watchlist config ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CBIndicator:
    canonical_name: str
    theme: str        # "Inflation" | "Labor" | "Growth"
    weight: float     # relative within-theme importance (3=primary, 2=secondary, 1=supplementary)
    direction: int    # +1 = higher → hawkish/bullish; -1 = higher → dovish/bearish
    notes: str = ""


# Mandate weights applied on top of theme scores (same as cb_reaction_score.py)
@dataclass(frozen=True)
class MandateWeights:
    inflation_weight: float
    labor_weight: float
    growth_weight: float
    mandate_type: str


MANDATE_WEIGHTS: dict[str, MandateWeights] = {
    "US": MandateWeights(1.50, 1.00, 0.50, "dual"),
    "EU": MandateWeights(2.00, 0.50, 0.50, "single"),
    "UK": MandateWeights(1.80, 0.70, 0.50, "inflation"),
    "JP": MandateWeights(1.80, 0.50, 0.80, "dual"),
    "AU": MandateWeights(1.50, 1.00, 0.50, "dual"),
    "NZ": MandateWeights(1.50, 1.00, 0.50, "dual"),
    "CA": MandateWeights(1.80, 0.70, 0.50, "inflation"),
    "CH": MandateWeights(2.00, 0.30, 0.50, "single"),
}


# Each indicator carries: canonical_name, theme, weight (primary=3/secondary=2/supplementary=1),
# direction (+1 higher strengthens, -1 higher weakens).
CB_WATCHLIST: dict[str, list[CBIndicator]] = {

    # ── Fed (US) ──────────────────────────────────────────────────────────────
    # Dual mandate: price stability + maximum employment.
    # Primary inflation gauge: Core PCE (preferred over CPI since 2000).
    "US": [
        CBIndicator("core_pce_price_index_yoy", "Inflation", 3.0, +1,
                    "Fed's primary inflation gauge — targets 2%"),
        CBIndicator("pce_price_index_yoy",       "Inflation", 2.0, +1,
                    "Headline PCE — broader basket"),
        CBIndicator("core_cpi_yoy",              "Inflation", 1.0, +1,
                    "Core CPI — widely cited proxy before PCE release"),
        CBIndicator("unemployment_rate",         "Labor",     3.0, -1,
                    "Primary labor market gauge — dual mandate"),
        CBIndicator("nfp",                       "Labor",     3.0, +1,
                    "Non-Farm Payrolls — headline jobs print"),
        CBIndicator("avg_hourly_earnings_yoy",   "Labor",     2.0, +1,
                    "Wage inflation — key for persistent inflation channel"),
        CBIndicator("ism_manufacturing_pmi",     "Growth",    2.0, +1,
                    "ISM Manufacturing — leading activity indicator"),
        CBIndicator("retail_sales_mom",          "Growth",    2.0, +1,
                    "Consumer spending — 70% of US GDP"),
        CBIndicator("sp_global_services_pmi",    "Growth",    1.0, +1,
                    "Services sector activity"),
    ],

    # ── ECB (EU) ──────────────────────────────────────────────────────────────
    # Single mandate: price stability (HICP < 2%, symmetrically).
    # Employment and growth are secondary considerations.
    "EU": [
        CBIndicator("core_cpi_yoy",               "Inflation", 3.0, +1,
                    "Core HICP — ECB's focus once supply shocks pass"),
        CBIndicator("cpi_headline_yoy",           "Inflation", 2.0, +1,
                    "Headline HICP — primary ECB mandate target"),
        CBIndicator("consumer_inflation_expectation", "Inflation", 1.0, +1,
                    "Inflation expectations — key for credibility"),
        CBIndicator("unemployment_rate",          "Labor",     3.0, -1,
                    "Euro-area unemployment — secondary mandate"),
        CBIndicator("gdp_qoq",                   "Growth",    2.0, +1,
                    "Euro-area GDP — output gap assessment"),
        CBIndicator("composite_pmi",             "Growth",    2.0, +1,
                    "Composite PMI — real-time activity proxy"),
        CBIndicator("manufacturing_pmi",         "Growth",    1.0, +1,
                    "Manufacturing PMI — industrial sector pulse"),
    ],

    # ── BoE (UK) ──────────────────────────────────────────────────────────────
    # Inflation target (2% CPI). Employment is implicit second mandate.
    # BoE places heavy weight on wage growth as a leading indicator for
    # sticky services inflation.
    "UK": [
        CBIndicator("cpi_headline_yoy",          "Inflation", 3.0, +1,
                    "UK CPI — primary 2% target"),
        CBIndicator("core_cpi_yoy",              "Inflation", 2.0, +1,
                    "Core CPI — strips volatile food & energy"),
        CBIndicator("avg_earnings_excl_bonus",   "Labor",     3.0, +1,
                    "Regular pay growth — BoE's primary wage gauge"),
        CBIndicator("unemployment_rate",         "Labor",     2.0, -1,
                    "UK unemployment"),
        CBIndicator("gdp_mom",                   "Growth",    2.0, +1,
                    "Monthly GDP — BoE uses MoM given high-frequency releases"),
        CBIndicator("services_pmi",              "Growth",    2.0, +1,
                    "Services PMI — UK is 80% services economy"),
        CBIndicator("retail_sales_mom",          "Growth",    1.0, +1,
                    "Retail sales — consumer demand signal"),
    ],

    # ── BoJ (Japan) ───────────────────────────────────────────────────────────
    # Price stability target (2% CPI). Also considers financial stability.
    # BoJ uses "core-core" CPI (ex food & energy) as their key measure.
    # Tokyo CPI is released ~3 weeks before national CPI — watched as
    # leading indicator.
    "JP": [
        CBIndicator("core_cpi_yoy",              "Inflation", 3.0, +1,
                    "Core CPI ex fresh food — BoJ's preferred measure"),
        CBIndicator("cpi_ex_food_energy_yoy",    "Inflation", 3.0, +1,
                    "Core-core CPI ex food & energy — BoJ's key watch"),
        CBIndicator("cpi_headline_yoy",          "Inflation", 1.0, +1,
                    "Headline CPI"),
        CBIndicator("unemployment_rate",         "Labor",     2.0, -1,
                    "Japan unemployment"),
        CBIndicator("gdp_qoq",                   "Growth",    2.0, +1,
                    "GDP QoQ — output gap matters for BoJ normalisation path"),
        CBIndicator("composite_pmi",             "Growth",    2.0, +1,
                    "Composite PMI — real-time activity"),
        CBIndicator("industrial_production_mom", "Growth",    1.0, +1,
                    "Industrial production — Japan is export-driven"),
    ],

    # ── RBA (Australia) ───────────────────────────────────────────────────────
    # Dual mandate: 2-3% CPI band + full employment.
    # RBA's preferred underlying inflation measure is Trimmed Mean CPI.
    "AU": [
        CBIndicator("rba_trimmed_mean_cpi_yoy",  "Inflation", 3.0, +1,
                    "RBA Trimmed Mean — primary underlying inflation measure"),
        CBIndicator("monthly_cpi_indicator",     "Inflation", 2.0, +1,
                    "Monthly CPI Indicator — higher frequency inflation read"),
        CBIndicator("cpi_headline_yoy",          "Inflation", 1.0, +1,
                    "Headline CPI QoQ"),
        CBIndicator("unemployment_rate",         "Labor",     3.0, -1,
                    "Unemployment — dual mandate"),
        CBIndicator("employment_change",         "Labor",     2.0, +1,
                    "Employment change — monthly jobs print"),
        CBIndicator("composite_pmi",             "Growth",    2.0, +1,
                    "Composite PMI"),
        CBIndicator("retail_sales_mom",          "Growth",    2.0, +1,
                    "Retail sales — consumer demand"),
    ],

    # ── RBNZ (New Zealand) ────────────────────────────────────────────────────
    # Dual mandate: 1-3% CPI band + maximum sustainable employment.
    # NZ data is quarterly — so fewer data points per year.
    "NZ": [
        CBIndicator("cpi_headline_yoy",          "Inflation", 3.0, +1,
                    "NZ CPI YoY — primary RBNZ target"),
        CBIndicator("cpi_headline_qoq",          "Inflation", 2.0, +1,
                    "CPI QoQ — RBNZ uses quarterly data primarily"),
        CBIndicator("unemployment_rate",         "Labor",     3.0, -1,
                    "Unemployment — dual mandate"),
        CBIndicator("employment_change_qoq",     "Labor",     2.0, +1,
                    "Employment change QoQ"),
        CBIndicator("labour_cost_index_yoy",     "Labor",     2.0, +1,
                    "Labour Cost Index — wage inflation proxy"),
        CBIndicator("business_confidence",       "Growth",    2.0, +1,
                    "Business confidence — forward-looking activity signal"),
        CBIndicator("balance_of_trade",          "Growth",    1.0, +1,
                    "Trade balance — NZ is commodity export dependent"),
    ],

    # ── BoC (Canada) ──────────────────────────────────────────────────────────
    # Inflation target (2% midpoint, 1-3% band).
    # BoC explicitly monitors three core measures:
    # CPI-Trim, CPI-Median, CPI-Common — all three included.
    "CA": [
        CBIndicator("cpi_trimmed_mean_yoy",      "Inflation", 3.0, +1,
                    "CPI-Trim — BoC preferred core measure #1"),
        CBIndicator("cpi_median_yoy",            "Inflation", 3.0, +1,
                    "CPI-Median — BoC preferred core measure #2"),
        CBIndicator("cpi_common_yoy",            "Inflation", 3.0, +1,
                    "CPI-Common — BoC preferred core measure #3"),
        CBIndicator("cpi_headline_yoy",          "Inflation", 1.0, +1,
                    "Headline CPI"),
        CBIndicator("unemployment_rate",         "Labor",     3.0, -1,
                    "Canada unemployment"),
        CBIndicator("employment_change",         "Labor",     2.0, +1,
                    "Employment change"),
        CBIndicator("gdp_mom",                   "Growth",    3.0, +1,
                    "Monthly GDP — Canada has monthly GDP unlike most G10"),
        CBIndicator("ivey_pmi_sa",               "Growth",    2.0, +1,
                    "Ivey PMI — BoC-watched activity gauge"),
        CBIndicator("retail_sales_mom",          "Growth",    1.0, +1,
                    "Retail sales"),
    ],

    # ── SNB (Switzerland) ─────────────────────────────────────────────────────
    # Price stability (0-2% CPI). Exchange rate is a secondary tool.
    # SNB meets quarterly — fewer decision points.
    "CH": [
        CBIndicator("cpi_headline_yoy",          "Inflation", 3.0, +1,
                    "Swiss CPI — primary SNB target (0-2% range)"),
        CBIndicator("producer_import_prices_yoy", "Inflation", 2.0, +1,
                    "Producer & Import Prices — upstream inflation pipeline"),
        CBIndicator("unemployment_rate",         "Labor",     2.0, -1,
                    "Swiss unemployment"),
        CBIndicator("gdp_qoq",                   "Growth",    2.0, +1,
                    "Swiss GDP"),
        CBIndicator("retail_sales_yoy",          "Growth",    1.0, +1,
                    "Retail sales"),
    ],
}

CB_PREFERRED_TABLES = (
    "processed.cb_preferred_score",
    "processed.cb_preferred_rankings",
    "processed.cb_preferred_components",
)

STALENESS_DAYS = 92  # max days an indicator value stays valid (≈ 3 months)


@dataclass(frozen=True)
class CBPreferredConfig:
    strong_threshold: float = 1.00
    mild_threshold: float = 0.35
    trend_window_days: int = 30


# ── Entry point ───────────────────────────────────────────────────────────────

async def build_cb_preferred_score(
    output_dir: Path | str = Path("data/cb_preferred"),
    config: CBPreferredConfig = CBPreferredConfig(),
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    async with session_scope() as session:
        await _create_schema(session)

        print("  Loading indicator features…")
        features_df = await _load_features(session)
        if features_df.empty:
            return {"error": "No indicator features found."}

        print("  Computing CB-preferred scores…")
        scores_df, components_df = _compute_scores(features_df, config)
        if scores_df.empty:
            return {"error": "No scores computed — check CB_WATCHLIST canonical names."}

        print(f"  Writing {len(scores_df)} score rows to DB…")
        await _insert_scores(session, scores_df)
        await _build_rankings(session)
        await _insert_components(session, components_df)

        summary = await _build_summary(session)

        for table in CB_PREFERRED_TABLES:
            await _export_csv(
                session,
                table,
                output_path / f"{table.split('.')[-1]}.csv",
            )
        _write_json(output_path / "cb_preferred_report.json", summary)

    return {
        "output_dir": str(output_path),
        "tables": list(CB_PREFERRED_TABLES),
        "summary": summary,
    }


# ── Data loading ──────────────────────────────────────────────────────────────

async def _load_features(session: AsyncSession) -> pd.DataFrame:
    """Load indicator_features rows for all CB-preferred canonical names."""
    all_pairs: list[tuple[str, str]] = [
        (country, ind.canonical_name)
        for country, indicators in CB_WATCHLIST.items()
        for ind in indicators
    ]

    # Build a VALUES list for the IN filter
    values_sql = ", ".join(f"('{cc}', '{cn}')" for cc, cn in all_pairs)

    rows = await _fetch_all(
        session,
        f"""
        SELECT
            f.indicator_id,
            f.country_code,
            f.currency_code,
            i.canonical_name,
            f.release_date_utc          AS score_date,
            f.release_timestamp_utc,
            COALESCE(
                f.standardized_surprise_score,
                f.expanding_z_score,
                f.momentum,
                f.mom_change
            )                           AS signal,
            f.standardized_surprise_score,
            f.expanding_z_score,
            f.momentum
        FROM processed.indicator_features f
        JOIN public.indicators i ON i.id = f.indicator_id
        WHERE (f.country_code, i.canonical_name) IN ({values_sql})
        ORDER BY f.country_code, i.canonical_name, f.release_date_utc
        """,
    )
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["score_date"] = pd.to_datetime(df["score_date"]).dt.date
    df["release_timestamp_utc"] = pd.to_datetime(df["release_timestamp_utc"], utc=True)
    df["signal"] = pd.to_numeric(df["signal"], errors="coerce")
    return df


# ── Score computation (pandas) ────────────────────────────────────────────────

def _compute_scores(
    features_df: pd.DataFrame,
    config: CBPreferredConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute CB-preferred strength scores and component breakdown."""

    # Build config lookup: (country_code, canonical_name) → (theme, weight, direction)
    config_rows = []
    for country, indicators in CB_WATCHLIST.items():
        for ind in indicators:
            config_rows.append({
                "country_code":  country,
                "canonical_name": ind.canonical_name,
                "theme":          ind.theme,
                "weight":         ind.weight,
                "direction":      ind.direction,
                "notes":          ind.notes,
            })
    config_df = pd.DataFrame(config_rows)

    # All unique score dates (every date any preferred indicator was released)
    all_score_dates = sorted(features_df["score_date"].unique())
    stale_cutoff = timedelta(days=STALENESS_DAYS)

    score_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []

    for score_date in all_score_dates:
        for country_code, indicators in CB_WATCHLIST.items():
            # Slice: only this country, only data released on or before score_date
            # and within the staleness window
            cutoff = score_date - stale_cutoff
            country_data = features_df[
                (features_df["country_code"] == country_code)
                & (features_df["score_date"] <= score_date)
                & (features_df["score_date"] > cutoff)
            ]

            theme_numerators: dict[str, float] = {}
            theme_denominators: dict[str, float] = {}
            contrib_count: dict[str, int] = {}

            for ind in indicators:
                ind_data = country_data[
                    country_data["canonical_name"] == ind.canonical_name
                ]
                if ind_data.empty:
                    continue

                # Most recent release within window
                latest = ind_data.sort_values("score_date").iloc[-1]
                raw_signal = latest["signal"]
                if pd.isna(raw_signal):
                    continue

                directional = float(raw_signal) * ind.direction
                weighted    = directional * ind.weight

                theme_numerators[ind.theme]   = theme_numerators.get(ind.theme, 0.0)   + weighted
                theme_denominators[ind.theme] = theme_denominators.get(ind.theme, 0.0) + ind.weight
                contrib_count[ind.theme]      = contrib_count.get(ind.theme, 0) + 1

                component_rows.append({
                    "score_date":     score_date,
                    "country_code":   country_code,
                    "currency_code":  latest.get("currency_code", ""),
                    "theme":          ind.theme,
                    "canonical_name": ind.canonical_name,
                    "raw_signal":     round(float(raw_signal), 6),
                    "direction":      ind.direction,
                    "weight":         ind.weight,
                    "directional_signal": round(directional, 6),
                    "weighted_signal":    round(weighted, 6),
                    "notes":          ind.notes,
                })

            if not theme_numerators:
                continue

            theme_scores: dict[str, float | None] = {
                "Inflation": None,
                "Labor":     None,
                "Growth":    None,
            }
            for theme, num in theme_numerators.items():
                denom = theme_denominators[theme]
                if denom > 0:
                    theme_scores[theme] = num / denom

            mw = MANDATE_WEIGHTS.get(country_code)
            if mw is None:
                continue

            pressure_num = 0.0
            pressure_den = 0.0
            for theme, tw in (
                ("Inflation", mw.inflation_weight),
                ("Labor",     mw.labor_weight),
                ("Growth",    mw.growth_weight),
            ):
                ts = theme_scores[theme]
                if ts is not None:
                    pressure_num += ts * tw
                    pressure_den += tw

            if pressure_den == 0:
                continue

            cb_pressure = pressure_num / pressure_den
            categories_available = sum(1 for v in theme_scores.values() if v is not None)
            total_contributors   = sum(contrib_count.values())

            currency_code = features_df[
                features_df["country_code"] == country_code
            ]["currency_code"].iloc[0] if not features_df[
                features_df["country_code"] == country_code
            ].empty else ""

            score_rows.append({
                "date":               score_date,
                "country_code":       country_code,
                "currency":           currency_code,
                "inflation_score":    theme_scores["Inflation"],
                "labor_score":        theme_scores["Labor"],
                "growth_score":       theme_scores["Growth"],
                "inflation_contributors": contrib_count.get("Inflation", 0),
                "labor_contributors":     contrib_count.get("Labor", 0),
                "growth_contributors":    contrib_count.get("Growth", 0),
                "inflation_weight":   mw.inflation_weight,
                "labor_weight":       mw.labor_weight,
                "growth_weight":      mw.growth_weight,
                "mandate_type":       mw.mandate_type,
                "cb_pressure_normalized": round(cb_pressure, 6),
                "categories_available":   categories_available,
                "total_contributors":     total_contributors,
            })

    if not score_rows:
        return pd.DataFrame(), pd.DataFrame()

    scores_df = pd.DataFrame(score_rows)

    # Cross-currency z-score per date
    def _zscore(x: pd.Series) -> pd.Series:
        mu, sigma = x.mean(), x.std()
        if sigma == 0 or pd.isna(sigma):
            return pd.Series(0.0, index=x.index)
        return (x - mu) / sigma

    scores_df["cb_strength_score"] = scores_df.groupby("date")[
        "cb_pressure_normalized"
    ].transform(_zscore)

    # Direction score: compare current pressure to pressure 30 days ago
    scores_df = scores_df.sort_values(["country_code", "date"])
    scores_df["prior_pressure"] = scores_df.groupby("country_code")[
        "cb_pressure_normalized"
    ].shift(config.trend_window_days)
    scores_df["direction_score"] = (
        scores_df["cb_pressure_normalized"] - scores_df["prior_pressure"]
    )

    # Labels
    scores_df["strength_label"] = scores_df["cb_strength_score"].apply(
        lambda z: _strength_label(z, config)
    )
    scores_df["trend_label"] = scores_df["direction_score"].apply(
        lambda d: "improving" if d > 0.05 else ("weakening" if d < -0.05 else "flat")
        if pd.notna(d) else "flat"
    )
    scores_df["confidence"] = scores_df.apply(
        lambda r: _confidence(r["categories_available"], r["total_contributors"]),
        axis=1,
    )

    components_df = pd.DataFrame(component_rows) if component_rows else pd.DataFrame()
    return scores_df, components_df


def _strength_label(z: float, config: CBPreferredConfig) -> str:
    if pd.isna(z):
        return "neutral"
    if z >= config.strong_threshold:
        return "strong"
    if z >= config.mild_threshold:
        return "mild_strong"
    if z <= -config.strong_threshold:
        return "weak"
    if z <= -config.mild_threshold:
        return "mild_weak"
    return "neutral"


def _confidence(categories: int, contributors: int) -> str:
    if categories == 3 and contributors >= 6:
        return "high"
    if categories >= 2 and contributors >= 3:
        return "medium"
    return "low"


# ── DB writes ─────────────────────────────────────────────────────────────────

async def _create_schema(session: AsyncSession) -> None:
    stmts = [
        "CREATE SCHEMA IF NOT EXISTS processed",
        "DROP TABLE IF EXISTS processed.cb_preferred_components",
        "DROP TABLE IF EXISTS processed.cb_preferred_rankings",
        "DROP TABLE IF EXISTS processed.cb_preferred_score",
        """
        CREATE TABLE processed.cb_preferred_score (
            score_id              bigserial PRIMARY KEY,
            date                  date NOT NULL,
            country_code          varchar(2) NOT NULL,
            currency              varchar(3) NOT NULL,
            inflation_score       numeric(20,6),
            labor_score           numeric(20,6),
            growth_score          numeric(20,6),
            inflation_contributors integer NOT NULL DEFAULT 0,
            labor_contributors     integer NOT NULL DEFAULT 0,
            growth_contributors    integer NOT NULL DEFAULT 0,
            inflation_weight      numeric(10,4) NOT NULL,
            labor_weight          numeric(10,4) NOT NULL,
            growth_weight         numeric(10,4) NOT NULL,
            mandate_type          text NOT NULL,
            cb_pressure_normalized numeric(20,6) NOT NULL,
            cb_strength_score     numeric(20,6) NOT NULL,
            direction_score       numeric(20,6),
            strength_label        text NOT NULL,
            trend_label           text NOT NULL,
            confidence            text NOT NULL,
            categories_available  integer NOT NULL,
            total_contributors    integer NOT NULL,
            processed_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (date, country_code)
        )
        """,
        """
        CREATE TABLE processed.cb_preferred_rankings (
            ranking_id          bigserial PRIMARY KEY,
            date                date NOT NULL,
            currency            varchar(3) NOT NULL,
            country_code        varchar(2) NOT NULL,
            cb_strength_score   numeric(20,6) NOT NULL,
            cb_pressure_normalized numeric(20,6) NOT NULL,
            strength_label      text NOT NULL,
            trend_label         text NOT NULL,
            confidence          text NOT NULL,
            rank_strongest      integer NOT NULL,
            rank_weakest        integer NOT NULL,
            percentile_rank     numeric(20,6),
            processed_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (date, currency)
        )
        """,
        """
        CREATE TABLE processed.cb_preferred_components (
            component_id     bigserial PRIMARY KEY,
            score_date       date NOT NULL,
            country_code     varchar(2) NOT NULL,
            currency_code    varchar(3),
            theme            text NOT NULL,
            canonical_name   text NOT NULL,
            raw_signal       numeric(20,6),
            direction        integer NOT NULL,
            weight           numeric(10,4) NOT NULL,
            directional_signal numeric(20,6),
            weighted_signal    numeric(20,6),
            notes            text,
            processed_at     timestamptz NOT NULL DEFAULT now()
        )
        """,
    ]
    for stmt in stmts:
        await session.execute(text(stmt))


async def _insert_scores(session: AsyncSession, df: pd.DataFrame) -> None:
    cols = [
        "date", "country_code", "currency",
        "inflation_score", "labor_score", "growth_score",
        "inflation_contributors", "labor_contributors", "growth_contributors",
        "inflation_weight", "labor_weight", "growth_weight",
        "mandate_type", "cb_pressure_normalized", "cb_strength_score",
        "direction_score", "strength_label", "trend_label", "confidence",
        "categories_available", "total_contributors",
    ]
    for _, row in df.iterrows():
        await session.execute(
            text(
                """
                INSERT INTO processed.cb_preferred_score
                    (date, country_code, currency,
                     inflation_score, labor_score, growth_score,
                     inflation_contributors, labor_contributors, growth_contributors,
                     inflation_weight, labor_weight, growth_weight,
                     mandate_type, cb_pressure_normalized, cb_strength_score,
                     direction_score, strength_label, trend_label, confidence,
                     categories_available, total_contributors)
                VALUES
                    (:date, :country_code, :currency,
                     :inflation_score, :labor_score, :growth_score,
                     :inflation_contributors, :labor_contributors, :growth_contributors,
                     :inflation_weight, :labor_weight, :growth_weight,
                     :mandate_type, :cb_pressure_normalized, :cb_strength_score,
                     :direction_score, :strength_label, :trend_label, :confidence,
                     :categories_available, :total_contributors)
                ON CONFLICT (date, country_code) DO UPDATE SET
                    cb_strength_score      = EXCLUDED.cb_strength_score,
                    cb_pressure_normalized = EXCLUDED.cb_pressure_normalized,
                    strength_label         = EXCLUDED.strength_label,
                    trend_label            = EXCLUDED.trend_label,
                    confidence             = EXCLUDED.confidence,
                    processed_at           = now()
                """
            ),
            {c: _safe(row.get(c)) for c in cols},
        )
    await session.execute(
        text(
            "CREATE INDEX idx_cb_preferred_score_date ON processed.cb_preferred_score (date, cb_strength_score DESC)"
        )
    )


async def _build_rankings(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            INSERT INTO processed.cb_preferred_rankings (
                date, currency, country_code,
                cb_strength_score, cb_pressure_normalized,
                strength_label, trend_label, confidence,
                rank_strongest, rank_weakest, percentile_rank
            )
            SELECT
                date, currency, country_code,
                cb_strength_score, cb_pressure_normalized,
                strength_label, trend_label, confidence,
                dense_rank()   OVER (PARTITION BY date ORDER BY cb_strength_score DESC),
                dense_rank()   OVER (PARTITION BY date ORDER BY cb_strength_score ASC),
                percent_rank() OVER (PARTITION BY date ORDER BY cb_strength_score)
            FROM processed.cb_preferred_score
            """
        )
    )


async def _insert_components(
    session: AsyncSession, df: pd.DataFrame
) -> None:
    if df.empty:
        return
    for _, row in df.iterrows():
        await session.execute(
            text(
                """
                INSERT INTO processed.cb_preferred_components
                    (score_date, country_code, currency_code, theme,
                     canonical_name, raw_signal, direction, weight,
                     directional_signal, weighted_signal, notes)
                VALUES
                    (:score_date, :country_code, :currency_code, :theme,
                     :canonical_name, :raw_signal, :direction, :weight,
                     :directional_signal, :weighted_signal, :notes)
                """
            ),
            {
                "score_date":   row["score_date"],
                "country_code": row["country_code"],
                "currency_code": row.get("currency_code", ""),
                "theme":        row["theme"],
                "canonical_name": row["canonical_name"],
                "raw_signal":   _safe(row.get("raw_signal")),
                "direction":    int(row["direction"]),
                "weight":       float(row["weight"]),
                "directional_signal": _safe(row.get("directional_signal")),
                "weighted_signal":    _safe(row.get("weighted_signal")),
                "notes":        row.get("notes", ""),
            },
        )


# ── Summary ───────────────────────────────────────────────────────────────────

async def _build_summary(session: AsyncSession) -> dict[str, Any]:
    summary = await _fetch_one(
        session,
        """
        SELECT
            (SELECT count(*)   FROM processed.cb_preferred_score)    AS score_rows,
            (SELECT count(*)   FROM processed.cb_preferred_rankings)  AS ranking_rows,
            (SELECT min(date)  FROM processed.cb_preferred_score)     AS first_date,
            (SELECT max(date)  FROM processed.cb_preferred_score)     AS last_date,
            (SELECT count(DISTINCT currency) FROM processed.cb_preferred_score) AS currencies
        """,
    )
    latest = await _fetch_all(
        session,
        """
        WITH latest AS (SELECT max(date) AS date FROM processed.cb_preferred_rankings)
        SELECT r.date, r.currency, r.country_code,
               r.cb_strength_score, r.cb_pressure_normalized,
               r.strength_label, r.trend_label, r.confidence, r.rank_strongest
        FROM processed.cb_preferred_rankings r
        JOIN latest l ON l.date = r.date
        ORDER BY r.rank_strongest
        """,
    )
    coverage = await _fetch_all(
        session,
        """
        SELECT country_code,
               max(inflation_contributors) AS infl_indicators,
               max(labor_contributors)     AS labor_indicators,
               max(growth_contributors)    AS growth_indicators
        FROM processed.cb_preferred_score
        WHERE date = (SELECT max(date) FROM processed.cb_preferred_score)
        GROUP BY country_code
        ORDER BY country_code
        """,
    )
    return {
        "summary":         summary,
        "latest_rankings": latest,
        "indicator_coverage": coverage,
        "watchlist_size":  {cc: len(inds) for cc, inds in CB_WATCHLIST.items()},
        "generated_at":    datetime.utcnow().isoformat() + "Z",
    }


# ── I/O helpers ───────────────────────────────────────────────────────────────

async def _export_csv(
    session: AsyncSession, table_name: str, path: Path
) -> None:
    rows = await _fetch_all(session, f"SELECT * FROM {table_name} ORDER BY 1")
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_value(row[k]) for k in headers})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, default=json_default, indent=2),
        encoding="utf-8",
    )


async def _fetch_all(
    session: AsyncSession,
    stmt: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result = await session.execute(text(stmt), params or {})
    return [dict(row) for row in result.mappings().all()]


async def _fetch_one(
    session: AsyncSession,
    stmt: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = await session.execute(text(stmt), params or {})
    row = result.mappings().one_or_none()
    return dict(row) if row else {}


def _safe(v: Any) -> Any:
    if v is None:
        return None
    try:
        if math.isnan(float(v)):
            return None
    except (TypeError, ValueError):
        pass
    return v
