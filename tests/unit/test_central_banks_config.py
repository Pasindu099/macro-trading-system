"""Step 8 Part A: per-central-bank reference config."""

from pathlib import Path

import yaml

CONFIG = yaml.safe_load(Path("config/central_banks.yaml").read_text(encoding="utf-8"))["banks"]
REQUIRED = {"mandate", "inflation_target", "projections", "rate_path_type", "conditioning", "voting", "meetings", "minutes", "verify"}


def test_all_eight_banks_have_required_fields_and_valid_enums():
    assert set(CONFIG) == {"FED", "ECB", "BOE", "BOJ", "RBA", "BOC", "RBNZ", "SNB"}
    for bank, cfg in CONFIG.items():
        assert REQUIRED <= set(cfg), bank
        assert cfg["mandate"] in {"dual", "single_price_stability"}, bank
        assert cfg["rate_path_type"] in {"dots", "own_track", "none"}, bank
        assert cfg["conditioning"] in {"own_views", "market_path", "market_curve", "board_median", "constant_rate"}, bank
        assert {"name", "cadence"} <= set(cfg["projections"]), bank


def test_all_banks_verified():
    fed = CONFIG["FED"]
    assert fed["verify"] is False and fed["rate_path_type"] == "dots" and fed["mandate"] == "dual"
    assert fed["inflation_target"]["value"] == 2.0 and fed["projections"]["months"] == [3, 6, 9, 12]
    assert "{yyyymmdd}" in fed["projections"]["source_html"] and fed["minutes"]["lag_days"] == 21
    assert CONFIG["ECB"]["verify"] is False and CONFIG["ECB"]["voting"]["participants"] == 27
    assert all(cfg["verify"] is False for cfg in CONFIG.values())


def test_blackout_rules_well_formed_and_unverified_marked():
    for bank, cfg in CONFIG.items():
        rule = cfg["blackout"]
        assert isinstance(rule["verified"], bool), bank
        if rule["start"] is None:
            assert rule["verified"] is False, bank
            continue
        assert rule["start"]["anchor"] in {"first_day", "decision"}, bank
        assert {"days_before", "business_days_before", "weekday"} & set(rule["start"]), bank
        assert rule["end"]["days_after_decision"] >= 0, bank
    assert {b for b, c in CONFIG.items() if not c["blackout"]["verified"]} == {"BOJ", "RBNZ", "SNB"}


def test_meeting_schedules_cover_2026_and_2027():
    meetings = yaml.safe_load(Path("config/cb_meetings.yaml").read_text(encoding="utf-8"))
    for bank in CONFIG:
        years = {m["dt"][:4] for m in meetings[bank]["meetings"]}
        assert {"2026", "2027"} <= years, bank
