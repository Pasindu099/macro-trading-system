"""Step 8 Part 0: secrets never reach logs or stored error text."""

from __future__ import annotations

import io
import logging
from datetime import date

import httpx
import pytest

from app.ingestion.eodhd_client import EODHDClient, EODHDError
from app.log_redaction import RedactingFormatter, redact

KEY = "69e3b0FAKEKEY.12345678"


def test_redacts_query_keys_headers_and_tokens():
    text = (f"https://eodhd.com/api/economic-events?api_token={KEY}&country=US "
            f"https://api.stlouisfed.org/fred/series/observations?series_id=UNRATE&api_key={KEY}&file_type=json "
            f"{{'Authorization': 'Bearer {KEY}'}} X-Api-Key: {KEY} "
            "https://api.telegram.org/bot123456:AAH-secret_token/getUpdates sk-abcdefghijklmnopqrstuvwx")
    out = redact(text)
    assert KEY not in out and "AAH-secret_token" not in out and "sk-abcdefghijklmnop" not in out
    assert "country=US" in out and "series_id=UNRATE" in out  # non-secret params survive


def test_logged_httpx_error_url_contains_no_key():
    request = httpx.Request("GET", "https://eodhd.com/api/economic-events", params={"api_token": KEY, "country": "US"})
    response = httpx.Response(422, request=request)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test.redaction")
    logger.addHandler(handler)
    try:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.exception("EODHD failed: %s", exc)  # message AND traceback carry the URL
    finally:
        logger.removeHandler(handler)
    logged = stream.getvalue()
    assert "economic-events" in logged and KEY not in logged


@pytest.mark.asyncio
async def test_eodhd_client_error_message_contains_no_key(monkeypatch):
    from app.ingestion import eodhd_client

    monkeypatch.setattr(eodhd_client, "RETRY_DELAYS_SECONDS", (0.0,) * 8)
    transport = httpx.MockTransport(lambda request: httpx.Response(422, request=request))
    client = EODHDClient(api_key=KEY, max_retries=0)
    client._client = httpx.AsyncClient(transport=transport)
    try:
        with pytest.raises(EODHDError) as exc:
            await client.fetch_economic_events("US", date(2025, 1, 1), date(2025, 1, 31))
    finally:
        await client._client.aclose()
    assert KEY not in str(exc.value) and "api_token=***" in str(exc.value)


def test_configured_root_formatter_redacts():
    from app.logging_config import configure_logging

    configure_logging()
    assert all(isinstance(h.formatter, RedactingFormatter) for h in logging.getLogger().handlers)
