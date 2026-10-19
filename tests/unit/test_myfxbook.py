from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.services import myfxbook

OUTLOOK = {"error": False, "symbols": [
    {"name": "EURUSD", "longPercentage": 38, "shortPercentage": 62, "longPositions": 4100, "shortPositions": 6700,
     "avgLongPrice": "1.0821", "avgShortPrice": "1.1032"},
    {"name": "USDJPY", "longPercentage": "71.5", "shortPercentage": "28.5", "longPositions": 9000,
     "shortPositions": 3600, "avgLongPrice": 151.2, "avgShortPrice": 149.9},
    {"name": "XAUUSD", "longPercentage": 60, "shortPercentage": 40},  # not a G10 pair
]}


def _settings(monkeypatch, email="me@example.com", password="s3cret-pw"):
    monkeypatch.setattr(myfxbook, "get_settings",
                        lambda: SimpleNamespace(myfxbook_email=email, myfxbook_password=password))


class Api:
    """MockTransport for login.json / get-community-outlook.json with an expiring session."""

    def __init__(self, expire_after=None, fail_first=0):
        self.calls, self.sessions, self.expire_after, self.fail_first = [], 0, expire_after, fail_first
        self.outlooks = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.headers["user-agent"].startswith("ForexCompass/")
        if self.fail_first:
            self.fail_first -= 1
            return httpx.Response(503)
        if request.url.path.endswith("login.json"):
            self.sessions += 1
            return httpx.Response(200, json={"error": False, "session": f"S{self.sessions}"})
        self.outlooks += 1
        if self.expire_after is not None and self.outlooks > self.expire_after and \
                request.url.params["session"] == "S1":
            return httpx.Response(200, json={"error": True, "message": "Invalid session."})
        return httpx.Response(200, json=OUTLOOK)


async def _no_sleep(_):
    return None


def test_g10_pairs_are_28_market_convention():
    pairs = myfxbook.g10_pairs()
    assert len(pairs) == 28 and {"EURUSD", "USDJPY", "GBPUSD", "AUDNZD", "CADJPY", "CHFJPY"} <= set(pairs)
    assert "USDEUR" not in pairs


async def test_login_once_and_reuse_session(monkeypatch):
    _settings(monkeypatch)
    api = Api()
    client = myfxbook.MyfxbookClient(transport=httpx.MockTransport(api), sleep=_no_sleep)
    await client.outlook()
    await client.outlook()
    assert client.logins == 1 and api.sessions == 1 and api.outlooks == 2


async def test_relogin_on_expired_session(monkeypatch):
    _settings(monkeypatch)
    api = Api(expire_after=1)
    client = myfxbook.MyfxbookClient(transport=httpx.MockTransport(api), sleep=_no_sleep)
    await client.outlook()
    symbols = await client.outlook()  # S1 expired -> one re-login -> S2
    assert client.logins == 2 and symbols[0]["name"] == "EURUSD"


async def test_backoff_on_server_errors_then_success(monkeypatch):
    _settings(monkeypatch)
    delays = []

    async def sleep(seconds):
        delays.append(seconds)

    api = Api(fail_first=2)
    client = myfxbook.MyfxbookClient(transport=httpx.MockTransport(api), sleep=sleep)
    await client.outlook()
    assert delays == [2.0, 8.0]


async def test_missing_credentials_reported_not_raised(monkeypatch):
    _settings(monkeypatch, email="", password="")
    with pytest.raises(myfxbook.MyfxbookError, match="credentials not configured"):
        await myfxbook.MyfxbookClient().login()

    class Tracker:
        skipped = False

        def mark_skipped(self):
            self.skipped = True

    tracker = Tracker()

    class Logger:
        async def __aenter__(self):
            return tracker

        async def __aexit__(self, *_):
            return None

    monkeypatch.setattr("app.ingestion.run_logger.run_logger", lambda *_: Logger())
    result = await myfxbook.run_snapshot()
    assert result == {"status": "not_configured", "message": "Myfxbook credentials not configured"}
    assert tracker.skipped


async def test_errors_never_contain_credentials(monkeypatch, caplog):
    _settings(monkeypatch)

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = myfxbook.MyfxbookClient(transport=httpx.MockTransport(boom), sleep=_no_sleep)
    with pytest.raises(myfxbook.MyfxbookError) as error:
        await client.login()
    text = str(error.value) + caplog.text
    assert "s3cret-pw" not in text and "me@example.com" not in text and "ConnectError" in text


def test_snapshot_rows_keep_g10_and_report_missing():
    taken = datetime(2026, 10, 19, 12, 5, tzinfo=UTC)
    rows, missing = myfxbook.snapshot_rows(OUTLOOK["symbols"], taken)
    assert [row["pair"] for row in rows] == ["EURUSD", "USDJPY"]
    eur = rows[0]
    assert (eur["long_pct"], eur["short_pct"], eur["long_positions"], eur["avg_short_price"]) == (
        Decimal("38"), Decimal("62"), 4100, Decimal("1.1032"))
    assert len(missing) == 26 and "GBPUSD" in missing and "EURUSD" not in missing


def test_web_page_retail_sources_disabled_by_default():
    from app.settings import Settings
    from app.services import retail_sentiment

    assert Settings.model_fields["retail_web_sources_enabled"].default is False
    assert retail_sentiment.HEADERS["User-Agent"].startswith("ForexCompass/")
