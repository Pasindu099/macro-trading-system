from datetime import datetime, timezone
import asyncio

from app.ingestion.canonicalizer import Canonicalizer
from app.ingestion.ingest_service import quarantine_future_actual
from app.processing.macro_features import build_indicator_features, cap_surprise_z, surprise_std_scale
from app.processing.macro_dataset import rebuild_macro_observations


def _event(actual, previous, estimate, day, period="Sep"):
    return {
        "type": "Consumer Inflation Expectation", "country": "EU",
        "comparison": None, "date": f"2026-09-{day:02d} 09:00:00",
        "period": period, "actual": actual, "previous": previous,
        "estimate": estimate,
    }


def test_future_actual_is_quarantined_but_schedule_is_kept():
    canonical = Canonicalizer.from_default_config().canonicalize(_event(35.2, 33, 40, 29))
    assert canonical is not None
    future = quarantine_future_actual(canonical, datetime(2026, 9, 28, tzinfo=timezone.utc))
    assert future.actual is None
    assert future.released_at == canonical.released_at
    assert future.raw_payload["actual"] == 35.2
    assert future.raw_payload["quarantine_reason"] == "future_actual_before_release"
    assert quarantine_future_actual(canonical, canonical.released_at).actual == canonical.actual


def test_eu_percent_and_survey_balance_are_separate_indicators():
    canonicalizer = Canonicalizer.from_default_config()
    balance = canonicalizer.canonicalize(_event(35.2, 33, 40, 29))
    percent = canonicalizer.canonicalize(_event(3.0, 2.9, 3.0, 18, "Aug"))
    borderline = canonicalizer.canonicalize(_event(6.1, 12.1, 10.0, 29))
    low_balance = canonicalizer.canonicalize(_event(4.8, 6.0, 5.0, 29))
    assert balance.canonical_name == borderline.canonical_name == "consumer_price_expectations_balance"
    assert low_balance.canonical_name == "consumer_price_expectations_balance"
    assert percent.canonical_name == "consumer_inflation_expectation"


def test_series_specific_surprise_scale_and_cap():
    assert surprise_std_scale(0.1, 2.0) == 0.5
    assert surprise_std_scale(1.0, 20.0) == 5.0
    assert surprise_std_scale(2.0, 3.0) == 2.0
    assert cap_surprise_z(68.52) == 5
    assert cap_surprise_z(-7) == -5
    assert cap_surprise_z(4.36) == 4.36


def test_feature_query_uses_24_prior_observations_and_series_scale():
    class CaptureSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement, parameters=None):
            self.statements.append((str(statement), parameters))

            class Result:
                def all(self):
                    return []

            return Result()

    session = CaptureSession()
    asyncio.run(build_indicator_features(session))
    insert, parameters = next((sql, params) for sql, params in session.statements
                              if "INSERT INTO processed.indicator_features" in sql)
    assert "ROWS BETWEEN 24 PRECEDING AND 1 PRECEDING" in insert
    assert "prior_expanding_stddev * :long_run_fraction" in insert
    assert "prior_rolling_avg_6" in insert
    assert parameters == {"long_run_fraction": 0.25}


def test_processing_excludes_future_and_quarantined_actuals():
    class CaptureSession:
        def __init__(self):
            self.sql = []

        async def execute(self, statement):
            self.sql.append(str(statement))

    session = CaptureSession()
    asyncio.run(rebuild_macro_observations(session))
    insert = next(sql for sql in session.sql if "INSERT INTO processed.macro_observations" in sql)
    assert "r.released_at <= CURRENT_TIMESTAMP" in insert
    assert "r.raw_payload->>'quarantine_reason' IS NULL" in insert
