from datetime import date, datetime, timezone

import pytest

from app.services import policy_model
from app.services.policy_model import config, evaluate, implied_rate, parse_boc_valet, parse_oecd
from app.services.cb_summary import bank_config


NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def _entry(value, day=date(2026, 10, 1), confidence="high"):
    return {"value": value, "as_of": day.isoformat(), "source": "fixture", "confidence": confidence,
            "observed_at": NOW.isoformat()}


def test_rule_gap_sign_and_sensitivity_envelope():
    central, low, high = implied_rate(1, 3, 2, -1)
    assert central == 4.0
    assert low <= central <= high
    inputs = {"neutral_rate": _entry(1), "inflation": _entry(3), "target": _entry(2),
              "output_gap": _entry(-1), "market": _entry(3.5, NOW.date())}
    row = evaluate("FED", inputs, config(), NOW.date())
    assert row["status"] == "available" and row["model_rate"] == 4
    assert row["gap_bp"] == 50 and row["confidence"] == "high"


def test_missing_or_stale_input_never_produces_number():
    inputs = {"neutral_rate": None, "inflation": _entry(3), "target": _entry(2),
              "output_gap": _entry(-1, date(2024, 1, 1)), "market": _entry(3.5, NOW.date())}
    row = evaluate("BOE", inputs, config(), NOW.date())
    assert row["status"] == "unavailable"
    assert row["missing_inputs"] == ["neutral_rate", "output_gap stale"]
    assert row["model_rate"] is row["gap_bp"] is row["confidence"] is None


def test_oecd_current_year_vintage_and_all_countries():
    raw = "DATAFLOW,REF_AREA,MEASURE,FREQ,TIME_PERIOD,OBS_VALUE\n"
    raw += "OECD.ECO.MAD:DSD_EO@DF_EO(1.5),JPN,GAP,A,2026,-0.25\n"
    raw += "OECD.ECO.MAD:DSD_EO@DF_EO(1.5),JPN,GAP,A,2027,1.0\n"
    parsed = parse_oecd(raw, config(), 2026, NOW)
    assert parsed["JPN"]["value"] == -0.25
    assert parsed["JPN"]["reference_year"] == 2026
    assert len(parsed) == 1


def test_low_confidence_source_caps_model_confidence():
    inputs = {"neutral_rate": _entry(-.25, confidence="low"), "inflation": _entry(2),
              "target": _entry(2), "output_gap": _entry(-.5, confidence="medium"),
              "market": _entry(1.25, NOW.date())}
    assert evaluate("BOJ", inputs, config(), NOW.date())["confidence"] == "low"


def test_dated_bank_neutral_values_and_older_snb_source():
    cfg = config()
    assert cfg["banks"]["BOE"]["neutral"] == "boe_taylor_real_midpoint"
    assert cfg["neutral_fallbacks"]["boe_taylor_real_midpoint"]["value"] == .875
    for bank in ("BOE", "RBA", "RBNZ", "SNB"):
        item = cfg["neutral_fallbacks"][cfg["banks"][bank]["neutral"]]
        assert item["source_url"].startswith("https://") and item["confidence"] == "low"
    inputs = {"neutral_rate": {**_entry(0, date(2024, 5, 30), "low"), "max_age_days": 1000},
              "inflation": _entry(1), "target": _entry(1), "output_gap": _entry(0),
              "market": _entry(0.5, NOW.date())}
    assert evaluate("SNB", inputs, cfg, NOW.date())["status"] == "available"


def test_snb_target_midpoint_preserves_band():
    target = bank_config()["SNB"]["inflation_target"]
    assert target["value"] == 1.0
    assert target["range"] == [0.0, 2.0]
    assert target["form"] == "band_midpoint"


@pytest.mark.asyncio
async def test_stale_ois_falls_back_to_dated_bill(monkeypatch):
    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

        def first(self):
            return self.rows[0] if self.rows else None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, query, params):
            if "ois_cache" in str(query):
                return Rows([{"curve_date": date(2026, 10, 1), "tenor_days": 90, "rate": 2.0}])
            assert params["country"] == "DE"
            return Rows([{"market_observation_date": date(2026, 10, 19),
                          "provider_symbol": "DE3M.GBOND", "value": 2.5}])

    monkeypatch.setattr(policy_model, "get_sessionmaker", lambda: lambda: Session())
    result = await policy_model._market("ECB", NOW.date(), NOW, 7)
    assert result["value"] == 2.5 and result["label"] == "bill-based proxy"
    assert result["confidence"] == "low"


def test_boc_valet_uses_both_preferred_core_measures_and_reference_date():
    payload = {"observations": [
        {"d": "2026-09-01", "CPI_TRIM": {"v": "1.8"}, "CPI_MEDIAN": {"v": "1.9"}},
        {"d": "2026-10-01", "CPI_TRIM": {"v": "9"}, "CPI_MEDIAN": {"v": "9"}},
    ]}
    item = parse_boc_valet(payload, config(), NOW.date(), NOW)
    assert item["value"] == 1.85
    assert item["components"] == {"CPI-trim": 1.8, "CPI-median": 1.9}
    assert item["as_of"] == "2026-09-01" and "Valet" in item["date_basis"]
    assert parse_boc_valet(payload, config(), date(2026, 9, 30), NOW) is None
