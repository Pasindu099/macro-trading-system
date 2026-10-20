from datetime import date, datetime, timezone

from app.services.policy_model import config, evaluate, implied_rate, parse_oecd


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
    assert cfg["banks"]["BOE"]["neutral"] == "nyfed_uk_unpublished"
    for bank in ("RBA", "RBNZ", "SNB"):
        item = cfg["neutral_fallbacks"][cfg["banks"][bank]["neutral"]]
        assert item["source_url"].startswith("https://") and item["confidence"] == "low"
    inputs = {"neutral_rate": {**_entry(0, date(2024, 5, 30), "low"), "max_age_days": 1000},
              "inflation": _entry(1), "target": _entry(1), "output_gap": _entry(0),
              "market": _entry(0.5, NOW.date())}
    assert evaluate("SNB", inputs, cfg, NOW.date())["status"] == "available"
