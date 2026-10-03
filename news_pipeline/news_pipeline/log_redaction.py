"""Copy of app/log_redaction.py (separate package; keep the two in sync). Strip secrets (API keys in URLs, auth headers, bearer tokens) from text before it is logged or stored.

Used by the root log formatter (covers every HTTP client, including httpx
exception messages and tracebacks that embed request URLs) and wherever error
strings are persisted, e.g. ingestion_runs.errors.
"""

from __future__ import annotations

import logging
import re

REDACTED = "***"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Query parameters: api_token=..., api_key=..., apikey=..., access_token=..., token=..., key=...
    (re.compile(r"(?i)\b((?:api[_-]?token|api[_-]?key|apikey|access[_-]?token|token|key|secret)=)[^&\s'\"<>]+"),
     rf"\g<1>{REDACTED}"),
    # Header dicts / reprs: 'Authorization': 'Bearer abc', authorization: Basic abc
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(?:bearer|basic|token)?\s*[^\s'\",}]+"),
     rf"\g<1>{REDACTED}"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), f"Bearer {REDACTED}"),
    # Header style X-Api-Key: value
    (re.compile(r"(?i)(x-api-key['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"), rf"\g<1>{REDACTED}"),
    # Telegram bot tokens live in the URL path: /bot123456:ABC-def/
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), f"/bot{REDACTED}"),
    # OpenAI-style secret keys anywhere in text
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),
)


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the fully formatted record, traceback included."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
