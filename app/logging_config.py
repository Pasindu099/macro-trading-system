"""Centralized logging configuration.

Called once at application startup (main.py) and once at script startup
(scripts/*). All modules get their logger via logging.getLogger(__name__)
and logging is configured centrally here.

Design choices:
    - Timestamps in local time, formatted tersely.
    - Logger name prefix so you can trace which module logged a message.
    - INFO by default. Set LOG_LEVEL=DEBUG in .env for verbose.
    - Third-party libraries turned down (httpx/apscheduler/sqlalchemy get
      chatty at INFO; we keep them at WARNING).
    - httpx request-logging suppressed by default to avoid leaking API
      keys into logs (the EODHD URL contains the API key).
"""

from __future__ import annotations

import logging
import sys

from app.settings import get_settings


def configure_logging() -> None:
    """Set up handlers and levels for the whole app.

    Idempotent — safe to call more than once (script then app, or reloads).
    """
    settings = get_settings()
    root_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Root logger: format + level
    root = logging.getLogger()
    root.setLevel(root_level)

    # Remove any default handlers (prevents duplicate lines if logging.basicConfig
    # was called elsewhere, e.g. in an older script).
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(handler)

    # Turn down noisy libraries.
    # httpx INFO includes full URLs which contain our API key — WARNING hides that.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # APScheduler logs every job trigger at INFO; keep WARNING to reduce clutter.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    # SQLAlchemy logs every SQL at INFO if echo=True is set; keep it quiet.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    # Uvicorn has its own handlers; silence the duplicate root propagation.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)