from datetime import date

from app.services.ecb_series import EUR_EER_BROAD_DAILY, parse_series_csv


def test_ecb_daily_eer_parses_into_existing_series_store():
    csv = "KEY,TIME_PERIOD,OBS_VALUE\nEXR.D.E03.EUR.EN00.A,2026-10-01,129.12\n"
    assert parse_series_csv(csv, EUR_EER_BROAD_DAILY) == [(date(2026, 10, 1), 129.12)]
