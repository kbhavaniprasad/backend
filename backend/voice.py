"""
voice.py — Retell AI integration
Creates web call sessions via the Retell REST API.
Plain HTTP calls using httpx — no complicated SDK wrappers.
"""

from __future__ import annotations

import logging

import httpx

from config import config

logger = logging.getLogger(__name__)


async def create_web_call(agent_id: str | None = None) -> dict:
    """
    POST /v2/create-web-call → Retell API
    Returns a dict with 'access_token' and 'call_id' for the browser SDK.

    Raises:
        ValueError   — if credentials are missing
        httpx.HTTPError — if the Retell API returns an error
    """
    agent = agent_id or config.RETELL_AGENT_ID

    if not config.RETELL_API_KEY:
        raise ValueError("RETELL_API_KEY is not set in .env")
    if not agent:
        raise ValueError("RETELL_AGENT_ID is not set in .env")

    headers = {
        "Authorization": f"Bearer {config.RETELL_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{config.RETELL_BASE_URL}/v2/create-web-call",
            headers=headers,
            json={"agent_id": agent},
        )
        resp.raise_for_status()
        data = resp.json()

    logger.info("Web call created  call_id=%s  agent=%s", data.get("call_id"), agent)
    return data


async def get_agent_info(agent_id: str | None = None) -> dict:
    """
    GET /get-agent/{agent_id} → Retell API
    Returns agent name, voice, and other metadata.
    """
    agent = agent_id or config.RETELL_AGENT_ID
    headers = {"Authorization": f"Bearer {config.RETELL_API_KEY}"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{config.RETELL_BASE_URL}/get-agent/{agent}",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()
