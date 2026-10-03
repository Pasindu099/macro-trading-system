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


def test_fed_is_filled_and_verified_others_flagged():
    fed = CONFIG["FED"]
    assert fed["verify"] is False and fed["rate_path_type"] == "dots" and fed["mandate"] == "dual"
    assert fed["inflation_target"]["value"] == 2.0 and fed["projections"]["months"] == [3, 6, 9, 12]
    assert "{yyyymmdd}" in fed["projections"]["source_html"] and fed["minutes"]["lag_days"] == 21
    assert all(cfg["verify"] is True for bank, cfg in CONFIG.items() if bank != "FED")
