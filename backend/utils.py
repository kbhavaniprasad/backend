"""
utils.py — Utility helpers
In-memory rate limiter + standard response builders.
No external cache server needed.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from fastapi import HTTPException, Request

from config import config

logger = logging.getLogger(__name__)

# ── In-memory rate limit store ────────────────────────────────────────────────
# Maps client IP → list of request timestamps within the current window.
_request_log: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(request: Request) -> None:
    """
    Raise HTTP 429 if the caller has exceeded the configured rate limit.
    Works without Redis — just a Python dict.
    """
    ip = request.client.host  # type: ignore
    now = time.monotonic()
    window = config.RATE_LIMIT_WINDOW
    limit = config.RATE_LIMIT_REQUESTS

    # Drop timestamps outside the sliding window
    _request_log[ip] = [t for t in _request_log[ip] if now - t < window]

    if len(_request_log[ip]) >= limit:
        logger.warning("Rate limit exceeded for %s", ip)
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Limit: {limit} per {window}s.",
        )

    _request_log[ip].append(now)


# ── Standard response helpers ─────────────────────────────────────────────────

def ok(message: str, data: Any = None) -> dict:
    """Return a consistent success envelope."""
    return {"success": True, "message": message, "data": data, "error": None}


def err(message: str, error: str | None = None) -> dict:
    """Return a consistent error envelope."""
    return {"success": False, "message": message, "data": None, "error": error}
