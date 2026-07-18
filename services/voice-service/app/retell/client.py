"""
Retell AI Client
================
Primary interface for all Retell AI API operations.

Retell AI replaces the manual STT → LLM → TTS pipeline with a
fully-managed AI voice agent that handles:
  - Real-time speech recognition
  - LLM conversation management
  - Natural TTS synthesis
  - Call state management via webhooks

API Reference: https://docs.retellai.com
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Any, Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class RetellClient:
    """
    Async HTTP client wrapper for the Retell AI REST API.

    Retell AI handles the full voice AI pipeline:
    Customer Call → Retell STT → Retell LLM → Retell TTS → Customer
    """

    def __init__(self, settings: Settings):
        self.api_key = settings.retell_api_key
        self.agent_id = settings.retell_agent_id
        self.base_url = settings.retell_base_url
        self.webhook_secret = settings.retell_webhook_secret

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    # ── Outbound Calls ────────────────────────────────────────────────────────

    async def create_phone_call(
        self,
        to_number: str,
        from_number: str,
        lead_id: str,
        tenant_id: str,
        lead_data: dict[str, Any],
        override_agent_id: Optional[str] = None,
    ) -> dict:
        """
        Initiate an outbound phone call via Retell AI.

        Retell handles STT, LLM conversation, and TTS automatically.
        Dynamic variables are injected into the agent's prompt at call time.

        Args:
            to_number: Lead's phone number in E.164 format (+1234567890)
            from_number: Your Twilio/carrier number registered with Retell
            lead_id: Platform lead ID (injected into agent context)
            tenant_id: Multi-tenant identifier
            lead_data: Lead details injected as dynamic variables
            override_agent_id: Use a different agent than the default

        Returns:
            Retell call object with call_id, status, etc.
        """
        agent_id = override_agent_id or self.agent_id

        # Dynamic variables are injected into the Retell agent's prompt
        # These replace {{variable_name}} placeholders in your Retell agent config
        dynamic_variables = {
            "lead_id": lead_id,
            "tenant_id": tenant_id,
            "lead_first_name": lead_data.get("first_name", "there"),
            "lead_last_name": lead_data.get("last_name", ""),
            "lead_company": lead_data.get("company", ""),
            "lead_source": lead_data.get("source", ""),
            "lead_job_title": lead_data.get("job_title", ""),
            "call_timestamp": datetime.utcnow().isoformat(),
        }

        payload = {
            "from_number": from_number,
            "to_number": to_number,
            "agent_id": agent_id,
            "retell_llm_dynamic_variables": dynamic_variables,
            "metadata": {
                "lead_id": lead_id,
                "tenant_id": tenant_id,
                "platform": "ai-lead-platform",
            },
        }

        logger.info(
            "Creating Retell outbound call",
            extra={"lead_id": lead_id, "tenant_id": tenant_id, "to": to_number},
        )

        response = await self._client.post("/v2/create-phone-call", json=payload)
        response.raise_for_status()
        data = response.json()

        logger.info(
            "Retell call created",
            extra={"call_id": data.get("call_id"), "lead_id": lead_id},
        )
        return data

    # ── Web Calls (Browser/WebRTC) ────────────────────────────────────────────

    async def create_web_call(
        self,
        lead_id: str,
        tenant_id: str,
        lead_data: dict[str, Any],
        override_agent_id: Optional[str] = None,
    ) -> dict:
        """
        Create a web-based call (WebRTC) for browser/chat widget integration.

        Returns an access_token that the frontend SDK uses to connect.
        Used for website chat-to-call escalation.
        """
        agent_id = override_agent_id or self.agent_id

        dynamic_variables = {
            "lead_id": lead_id,
            "tenant_id": tenant_id,
            "lead_first_name": lead_data.get("first_name", "there"),
            "lead_source": "website",
            "call_timestamp": datetime.utcnow().isoformat(),
        }

        payload = {
            "agent_id": agent_id,
            "retell_llm_dynamic_variables": dynamic_variables,
            "metadata": {
                "lead_id": lead_id,
                "tenant_id": tenant_id,
            },
        }

        response = await self._client.post("/v2/create-web-call", json=payload)
        response.raise_for_status()
        return response.json()

    # ── Call Management ───────────────────────────────────────────────────────

    async def get_call(self, call_id: str) -> dict:
        """Retrieve call details and status."""
        response = await self._client.get(f"/v2/get-call/{call_id}")
        response.raise_for_status()
        return response.json()

    async def list_calls(
        self,
        limit: int = 50,
        sort_order: str = "descending",
        filter_criteria: Optional[dict] = None,
    ) -> list[dict]:
        """List all calls with optional filters."""
        params: dict[str, Any] = {
            "limit": limit,
            "sort_order": sort_order,
        }
        if filter_criteria:
            params.update(filter_criteria)

        response = await self._client.get("/v2/list-calls", params=params)
        response.raise_for_status()
        return response.json()

    async def end_call(self, call_id: str) -> dict:
        """Programmatically end an active call."""
        response = await self._client.post(f"/v2/end-call/{call_id}")
        response.raise_for_status()
        return response.json()

    # ── Agent Management ──────────────────────────────────────────────────────

    async def get_agent(self, agent_id: Optional[str] = None) -> dict:
        """Retrieve Retell agent configuration."""
        aid = agent_id or self.agent_id
        response = await self._client.get(f"/v2/get-agent/{aid}")
        response.raise_for_status()
        return response.json()

    async def update_agent(
        self, updates: dict[str, Any], agent_id: Optional[str] = None
    ) -> dict:
        """
        Update Retell agent configuration.
        Used by Agent B to push prompt improvements.

        Args:
            updates: Dict of fields to update (e.g., {'general_prompt': '...'})
            agent_id: Agent to update (defaults to platform agent)
        """
        aid = agent_id or self.agent_id
        response = await self._client.patch(f"/v2/update-agent/{aid}", json=updates)
        response.raise_for_status()

        logger.info("Retell agent updated", extra={"agent_id": aid, "fields": list(updates.keys())})
        return response.json()

    async def list_agents(self) -> list[dict]:
        """List all Retell agents for this API key."""
        response = await self._client.get("/v2/list-agents")
        response.raise_for_status()
        return response.json()

    async def create_agent(self, agent_config: dict) -> dict:
        """Create a new Retell agent (for tenant-specific agents)."""
        response = await self._client.post("/v2/create-agent", json=agent_config)
        response.raise_for_status()
        return response.json()

    # ── Phone Numbers ─────────────────────────────────────────────────────────

    async def list_phone_numbers(self) -> list[dict]:
        """List all phone numbers linked to Retell."""
        response = await self._client.get("/v2/list-phone-numbers")
        response.raise_for_status()
        return response.json()

    async def import_phone_number(
        self, phone_number: str, termination_uri: str, label: str = ""
    ) -> dict:
        """Import a Twilio/carrier phone number into Retell."""
        payload = {
            "phone_number": phone_number,
            "termination_uri": termination_uri,
            "label": label,
        }
        response = await self._client.post("/v2/import-phone-number", json=payload)
        response.raise_for_status()
        return response.json()

    # ── LLM Configuration ─────────────────────────────────────────────────────

    async def create_retell_llm(self, llm_config: dict) -> dict:
        """Create a custom Retell LLM configuration."""
        response = await self._client.post("/v2/create-retell-llm", json=llm_config)
        response.raise_for_status()
        return response.json()

    async def update_retell_llm(self, llm_id: str, updates: dict) -> dict:
        """
        Update Retell LLM prompt — called by Agent B when pushing learning updates.
        This is the key integration point for the self-improving feedback loop.
        """
        response = await self._client.patch(f"/v2/update-retell-llm/{llm_id}", json=updates)
        response.raise_for_status()
        logger.info("Retell LLM updated", extra={"llm_id": llm_id})
        return response.json()

    async def get_retell_llm(self, llm_id: str) -> dict:
        """Get Retell LLM configuration (includes general_prompt, states, etc.)."""
        response = await self._client.get(f"/v2/get-retell-llm/{llm_id}")
        response.raise_for_status()
        return response.json()

    # ── Webhook Verification ──────────────────────────────────────────────────

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify that a webhook was genuinely sent by Retell AI.
        Uses HMAC-SHA256 signature verification.
        """
        if not self.webhook_secret:
            logger.warning("Webhook secret not configured — skipping signature verification")
            return True

        expected = hmac.new(
            self.webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()
