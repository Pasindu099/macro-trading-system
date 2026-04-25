"""EODHD API client for fetching economic calendar events.

This module is responsible only for HTTP communication with EODHD. It returns
raw response dicts with no database interaction and no domain logic. Callers
(ingestion service, backfill script) handle validation, canonicalization, and
storage.

Usage:
    from app.ingestion.eodhd_client import EODHDClient

    async with EODHDClient() as client:
        events = await client.fetch_economic_events(
            country="US",
            from_date=date(2026, 4, 1),
            to_date=date(2026, 4, 15),
        )
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from types import TracebackType
from typing import Any, Self

import httpx

from app.settings import get_settings

logger = logging.getLogger(__name__)

# EODHD country codes for the countries we track. Events from other
# countries are silently discarded. See spec §4.2 for the rationale.
ALLOWED_COUNTRIES: frozenset[str] = frozenset({
    "US",  # United States — USD
    "EU",  # Eurozone — EUR
    "DE",  # Germany — EUR
    "FR",  # France — EUR
    "UK",  # United Kingdom — GBP (note: EODHD uses UK, not GB)
    "JP",  # Japan — JPY
    "AU",  # Australia — AUD
    "NZ",  # New Zealand — NZD
    "CA",  # Canada — CAD
    "CH",  # Switzerland — CHF
})

# Exponential backoff delays in seconds. First retry waits 1s, second 5s,
# third 30s. After that we give up and raise.
RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 5.0, 30.0)


class EODHDError(Exception):
    """Base exception for EODHD client errors."""


class EODHDRateLimitError(EODHDError):
    """Raised when EODHD returns HTTP 429 (rate limit exceeded)."""


class EODHDAuthError(EODHDError):
    """Raised when EODHD returns HTTP 401/403 (bad API key)."""


class EODHDClient:
    """Async client for the EODHD economic events API.

    Designed to be used as an async context manager so the underlying httpx
    connection pool is properly closed:

        async with EODHDClient() as client:
            events = await client.fetch_economic_events(...)

    If you need to reuse the client across many calls without closing, you
    can instantiate it directly and call .close() yourself — but the context
    manager pattern is preferred.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        """Initialize the client.

        All arguments are optional and fall back to settings. This makes
        tests easier: you can override any value without touching env vars.

        Args:
            api_key: EODHD API key. Defaults to settings.eodhd_api_key.
            base_url: EODHD base URL. Defaults to settings.eodhd_base_url.
            timeout_seconds: Per-request timeout. Defaults to settings.
            max_retries: Max retry attempts on failure. Defaults to settings.
        """
        settings = get_settings()
        self._api_key = api_key or settings.eodhd_api_key
        self._base_url = (base_url or settings.eodhd_base_url).rstrip("/")
        self._timeout = timeout_seconds or settings.http_timeout_seconds
        self._max_retries = max_retries or settings.http_max_retries

        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        """Open the underlying HTTP connection pool."""
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def close(self) -> None:
        """Close the HTTP client. Prefer using the context manager instead."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_economic_events(
        self,
        country: str,
        from_date: date,
        to_date: date,
        limit: int = 1000,
        filter_allowed_countries: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch economic calendar events from EODHD.

        Args:
            country: EODHD country code (e.g. "US", "UK"). Must be in
                ALLOWED_COUNTRIES unless you disable filter_allowed_countries.
            from_date: Start date (inclusive).
            to_date: End date (inclusive).
            limit: Max results to return (EODHD caps at 1000).
            filter_allowed_countries: If True (default), drop events whose
                country field isn't in our tracked-country allowlist. This shouldn't
                trigger in normal flow since we filter by country in the URL,
                but acts as a defensive second filter.

        Returns:
            List of event dicts as returned by EODHD. Schema per spec §6 —
            keys include type, country, date, actual, previous, estimate, etc.

        Raises:
            ValueError: If country is not in ALLOWED_COUNTRIES.
            EODHDAuthError: On HTTP 401/403.
            EODHDRateLimitError: On HTTP 429 after all retries exhausted.
            EODHDError: On other HTTP errors after all retries exhausted.
        """
        if country not in ALLOWED_COUNTRIES:
            raise ValueError(
                f"Country {country!r} is not in the tracked allowlist "
                f"{sorted(ALLOWED_COUNTRIES)}"
            )
        if from_date > to_date:
            raise ValueError(f"from_date ({from_date}) must be <= to_date ({to_date})")
        if not 1 <= limit <= 1000:
            raise ValueError(f"limit must be 1..1000, got {limit}")

        url = f"{self._base_url}/economic-events"
        params = {
            "api_token": self._api_key,
            "country": country,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "fmt": "json",
            "limit": limit,
        }

        logger.info(
            "Fetching EODHD events: country=%s from=%s to=%s",
            country, from_date, to_date,
        )

        raw_events = await self._request_with_retries(url, params)

        if filter_allowed_countries:
            before = len(raw_events)
            raw_events = [
                event for event in raw_events
                if event.get("country") in ALLOWED_COUNTRIES
            ]
            dropped = before - len(raw_events)
            if dropped > 0:
                logger.debug("Dropped %d events outside allowlist", dropped)

        logger.info("Fetched %d events for country=%s", len(raw_events), country)
        return raw_events

    async def fetch_financial_news(
        self,
        *,
        ticker: str | None = None,
        topic: str | None = None,
        limit: int = 10,
        offset: int = 0,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch financial news from EODHD.

        EODHD requires at least one of ticker (`s`) or topic (`t`).
        """
        if not ticker and not topic:
            raise ValueError("Either ticker or topic must be provided")
        if limit < 1 or limit > 1000:
            raise ValueError(f"limit must be 1..1000, got {limit}")
        if offset < 0:
            raise ValueError("offset must be >= 0")

        url = f"{self._base_url}/news"
        params: dict[str, Any] = {
            "api_token": self._api_key,
            "fmt": "json",
            "limit": limit,
            "offset": offset,
        }
        if ticker:
            params["s"] = ticker
        if topic:
            params["t"] = topic
        if from_date:
            params["from"] = from_date.isoformat()
        if to_date:
            params["to"] = to_date.isoformat()

        logger.info(
            "Fetching EODHD news: ticker=%s topic=%s limit=%d offset=%d",
            ticker, topic, limit, offset,
        )
        data = await self._request_with_retries(url, params)
        logger.info("Fetched %d news items", len(data))
        return data

    async def fetch_eod_history(
        self,
        symbol: str,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        period: str = "d",
    ) -> list[dict[str, Any]]:
        """Fetch EOD history for one symbol.

        Used for market time series such as EODHD government bond yield
        tickers (for example US10Y.GBOND).
        """
        if not symbol or "." not in symbol:
            raise ValueError("symbol must include an exchange suffix, e.g. US10Y.GBOND")
        if period not in {"d", "w", "m"}:
            raise ValueError("period must be one of: d, w, m")
        if from_date and to_date and from_date > to_date:
            raise ValueError(f"from_date ({from_date}) must be <= to_date ({to_date})")

        url = f"{self._base_url}/eod/{symbol}"
        params: dict[str, Any] = {
            "api_token": self._api_key,
            "fmt": "json",
            "period": period,
        }
        if from_date:
            params["from"] = from_date.isoformat()
        if to_date:
            params["to"] = to_date.isoformat()

        logger.info("Fetching EODHD history: symbol=%s period=%s", symbol, period)
        data = await self._request_with_retries(url, params)
        logger.info("Fetched %d EOD rows for symbol=%s", len(data), symbol)
        return data

    async def _request_with_retries(
        self,
        url: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Execute GET request with exponential backoff retry logic.

        Retries on transient failures (timeouts, 5xx errors, rate limits).
        Does NOT retry on 4xx auth errors — those won't fix themselves.

        Raises the final exception if all retries exhausted.
        """
        if self._client is None:
            raise RuntimeError(
                "EODHDClient must be used as an async context manager "
                "or have .connect() called first"
            )

        last_exception: Exception | None = None

        # Total attempts = 1 initial + len(RETRY_DELAYS_SECONDS) retries
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.get(url, params=params)

                if response.status_code in (401, 403):
                    raise EODHDAuthError(
                        f"EODHD auth failed (HTTP {response.status_code}). "
                        f"Check your EODHD_API_KEY."
                    )

                if response.status_code == 429:
                    # Rate limited. Retry if we have attempts left.
                    msg = f"EODHD rate limit hit (HTTP 429)"
                    if attempt < self._max_retries:
                        logger.warning("%s, will retry", msg)
                        last_exception = EODHDRateLimitError(msg)
                    else:
                        raise EODHDRateLimitError(msg)
                elif 500 <= response.status_code < 600:
                    msg = f"EODHD server error (HTTP {response.status_code})"
                    if attempt < self._max_retries:
                        logger.warning("%s, will retry", msg)
                        last_exception = EODHDError(msg)
                    else:
                        raise EODHDError(msg)
                else:
                    # Any other non-2xx — raise. EODHD returns JSON errors
                    # sometimes with HTTP 200, so we just check for status.
                    response.raise_for_status()

                    data = response.json()
                    if not isinstance(data, list):
                        raise EODHDError(
                            f"Unexpected EODHD response shape: expected list, "
                            f"got {type(data).__name__}"
                        )
                    return data

            except httpx.TimeoutException as exc:
                msg = f"EODHD request timed out after {self._timeout}s"
                if attempt < self._max_retries:
                    logger.warning("%s, will retry", msg)
                    last_exception = EODHDError(msg)
                else:
                    raise EODHDError(msg) from exc

            except httpx.HTTPError as exc:
                # Network-level errors (connection refused, DNS failure, etc.)
                msg = f"EODHD HTTP error: {exc}"
                if attempt < self._max_retries:
                    logger.warning("%s, will retry", msg)
                    last_exception = EODHDError(msg)
                else:
                    raise EODHDError(msg) from exc

            # If we got here we need to retry. Sleep before next attempt.
            if attempt < self._max_retries:
                delay = RETRY_DELAYS_SECONDS[
                    min(attempt, len(RETRY_DELAYS_SECONDS) - 1)
                ]
                logger.info("Retrying in %.1fs (attempt %d/%d)",
                            delay, attempt + 2, self._max_retries + 1)
                await asyncio.sleep(delay)

        # Defensive fallback — should never reach here in practice.
        if last_exception is not None:
            raise last_exception
        raise EODHDError("All retry attempts exhausted with no specific error")
