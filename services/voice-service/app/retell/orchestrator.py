"""
Retell AI Call Orchestrator
============================
Manages the full lifecycle of AI voice calls powered by Retell AI.

Call Flow:
  1. Lead created  →  Kafka 'lead.created' event
  2. Orchestrator  →  Retell API: create_phone_call()
  3. Retell AI     →  Dials lead via Twilio PSTN
  4. Lead answers  →  Retell STT + LLM + TTS in real-time
  5. Call ends     →  Retell webhook: call_ended / call_analyzed
  6. Orchestrator  →  Store transcript → Kafka 'conversation.completed'
  7. Agent B       →  Evaluate, learn, improve
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import aioredis
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import Settings
from app.retell.client import RetellClient
from app.retell.models import RetellCallObject, RetellDisconnectionReason

logger = logging.getLogger(__name__)

# Redis key patterns
ACTIVE_CALL_KEY    = "retell:active_call:{lead_id}"
CALL_ATTEMPT_KEY   = "retell:attempts:{lead_id}"
CALL_LOCK_KEY      = "retell:lock:{lead_id}"


class CallOrchestrator:
    """
    Orchestrates outbound AI voice calls using Retell AI.

    Responsibilities:
      - Prevent duplicate calls via Redis distributed lock
      - Track call attempt count per lead (max retries)
      - Store call records in MongoDB
      - Publish Kafka events at each call lifecycle stage
      - Handle retry scheduling for unanswered calls
    """

    def __init__(
        self,
        retell_client: RetellClient,
        redis: aioredis.Redis,
        db: AsyncIOMotorDatabase,
        settings: Settings,
    ):
        self.retell = retell_client
        self.redis = redis
        self.db = db
        self.settings = settings

    # ── Outbound Call Initiation ──────────────────────────────────────────────

    async def initiate_call(
        self,
        lead_id: str,
        tenant_id: str,
        phone_number: str,
        lead_data: dict,
        from_number: Optional[str] = None,
    ) -> dict | None:
        """
        Initiate an outbound Retell AI call to a lead.

        Implements:
          - Distributed lock to prevent race conditions
          - Attempt count enforcement (max retries per lead)
          - Active call deduplication

        Returns the Retell call object or None if call was skipped.
        """
        # 1. Distributed lock — prevent duplicate simultaneous calls
        lock_key = CALL_LOCK_KEY.format(lead_id=lead_id)
        lock_acquired = await self.redis.set(lock_key, "1", ex=30, nx=True)
        if not lock_acquired:
            logger.warning("Call lock already held for lead", extra={"lead_id": lead_id})
            return None

        try:
            # 2. Check for already-active call
            active_key = ACTIVE_CALL_KEY.format(lead_id=lead_id)
            if await self.redis.exists(active_key):
                logger.info("Call already active for lead", extra={"lead_id": lead_id})
                return None

            # 3. Check retry limit
            attempt_key = CALL_ATTEMPT_KEY.format(lead_id=lead_id)
            attempts = int(await self.redis.get(attempt_key) or 0)
            if attempts >= self.settings.max_retries_per_lead:
                logger.info(
                    "Max call attempts reached",
                    extra={"lead_id": lead_id, "attempts": attempts},
                )
                return None

            # 4. Pick from_number (tenant-specific or default)
            caller_number = from_number or self.settings.twilio_phone_number

            # 5. Create Retell call
            call_data = await self.retell.create_phone_call(
                to_number=phone_number,
                from_number=caller_number,
                lead_id=lead_id,
                tenant_id=tenant_id,
                lead_data=lead_data,
            )

            call_id = call_data["call_id"]

            # 6. Track active call in Redis (TTL = max call duration)
            await self.redis.set(
                active_key,
                call_id,
                ex=self.settings.call_timeout_seconds + 60,
            )

            # 7. Increment attempt counter
            await self.redis.incr(attempt_key)
            await self.redis.expire(attempt_key, 86400 * 7)  # 7 days

            # 8. Persist call record to MongoDB
            await self._save_call_record(
                call_id=call_id,
                lead_id=lead_id,
                tenant_id=tenant_id,
                phone_number=phone_number,
                attempt_number=attempts + 1,
                lead_data=lead_data,
            )

            logger.info(
                "Retell call initiated",
                extra={
                    "call_id": call_id,
                    "lead_id": lead_id,
                    "tenant_id": tenant_id,
                    "attempt": attempts + 1,
                },
            )
            return call_data

        finally:
            await self.redis.delete(lock_key)

    # ── Web Call (Browser WebRTC) ────────────────────────────────────────────

    async def initiate_web_call(
        self,
        lead_id: str,
        tenant_id: str,
        lead_data: dict,
    ) -> dict:
        """
        Create a WebRTC-based call for browser/website chat widget.
        Returns access_token for Retell Web SDK.
        """
        call_data = await self.retell.create_web_call(
            lead_id=lead_id,
            tenant_id=tenant_id,
            lead_data=lead_data,
        )
        await self._save_call_record(
            call_id=call_data["call_id"],
            lead_id=lead_id,
            tenant_id=tenant_id,
            phone_number=None,
            attempt_number=1,
            lead_data=lead_data,
            call_type="web_call",
        )
        return call_data

    # ── Retry Logic ───────────────────────────────────────────────────────────

    async def schedule_retry(
        self,
        lead_id: str,
        tenant_id: str,
        phone_number: str,
        lead_data: dict,
        delay_minutes: int = 15,
    ) -> None:
        """
        Schedule a retry call after a delay.
        Called when lead doesn't answer or call fails.

        Uses asyncio.sleep for simple delays.
        In production, use Celery/ARQ for persistent scheduling.
        """
        logger.info(
            "Scheduling call retry",
            extra={
                "lead_id": lead_id,
                "delay_minutes": delay_minutes,
            },
        )
        await asyncio.sleep(delay_minutes * 60)
        await self.initiate_call(
            lead_id=lead_id,
            tenant_id=tenant_id,
            phone_number=phone_number,
            lead_data=lead_data,
        )

    # ── Call Lifecycle Handlers ───────────────────────────────────────────────

    async def handle_call_started(self, call: RetellCallObject) -> None:
        """Update call status to 'active' when Retell confirms the call connected."""
        lead_id = self._extract_lead_id(call)
        if not lead_id:
            return

        await self.db.calls.update_one(
            {"call_id": call.call_id},
            {
                "$set": {
                    "status": "active",
                    "started_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        # Update lead status to 'contacted' via lead-service
        logger.info("Call started", extra={"call_id": call.call_id, "lead_id": lead_id})

    async def handle_call_ended(self, call: RetellCallObject) -> dict:
        """
        Process call-ended event.
        - Save transcript to MongoDB
        - Clear active call from Redis
        - Handle retry if no-answer
        """
        lead_id = self._extract_lead_id(call)
        tenant_id = call.metadata.get("tenant_id") if call.metadata else None

        duration_ms = call.duration_ms or 0
        disconnection = call.disconnection_reason

        # Check if lead answered at all
        no_answer = disconnection in (
            RetellDisconnectionReason.DIAL_NO_ANSWER,
            RetellDisconnectionReason.VOICEMAIL_REACHED,
            RetellDisconnectionReason.MACHINE_DETECTED,
            RetellDisconnectionReason.DIAL_BUSY,
            RetellDisconnectionReason.DIAL_FAILED,
        )

        # Build conversation record
        conversation_data = {
            "call_id": call.call_id,
            "lead_id": lead_id,
            "tenant_id": tenant_id,
            "channel": "voice",
            "status": "no_answer" if no_answer else "completed",
            "transcript": call.transcript,
            "transcript_object": (
                [u.model_dump() for u in call.transcript_object]
                if call.transcript_object
                else []
            ),
            "duration_seconds": duration_ms / 1000 if duration_ms else 0,
            "disconnection_reason": disconnection.value if disconnection else None,
            "recording_url": call.recording_url,
            "agent_id": call.agent_id,
            "retell_dynamic_variables": call.retell_llm_dynamic_variables,
            "ended_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        await self.db.calls.update_one(
            {"call_id": call.call_id},
            {"$set": conversation_data},
        )

        # Clear Redis active call tracker
        if lead_id:
            active_key = ACTIVE_CALL_KEY.format(lead_id=lead_id)
            await self.redis.delete(active_key)

        logger.info(
            "Call ended",
            extra={
                "call_id": call.call_id,
                "lead_id": lead_id,
                "no_answer": no_answer,
                "duration_ms": duration_ms,
            },
        )

        return conversation_data

    async def handle_call_analyzed(self, call: RetellCallObject) -> dict:
        """
        Process post-call analysis from Retell.
        This is the trigger for Agent B evaluation.

        Retell provides:
          - call_summary
          - user_sentiment / agent_sentiment
          - call_successful flag
          - custom_analysis_data (from your LLM tool calls)
        """
        call_analysis = call.call_analysis or {}
        lead_id = self._extract_lead_id(call)
        tenant_id = call.metadata.get("tenant_id") if call.metadata else None

        # Enrich stored call with analysis
        await self.db.calls.update_one(
            {"call_id": call.call_id},
            {
                "$set": {
                    "call_analysis": call_analysis,
                    "call_summary": call_analysis.get("call_summary"),
                    "user_sentiment": call_analysis.get("user_sentiment"),
                    "call_successful": call_analysis.get("call_successful"),
                    "analyzed_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        # Build full conversation payload for Agent B
        full_call_doc = await self.db.calls.find_one({"call_id": call.call_id})

        logger.info(
            "Call analyzed — triggering Agent B evaluation",
            extra={"call_id": call.call_id, "lead_id": lead_id},
        )

        return full_call_doc or {}

    # ── MongoDB Persistence ───────────────────────────────────────────────────

    async def _save_call_record(
        self,
        call_id: str,
        lead_id: str | None,
        tenant_id: str | None,
        phone_number: str | None,
        attempt_number: int,
        lead_data: dict,
        call_type: str = "phone_call",
    ) -> None:
        """Persist a new call record to MongoDB."""
        record = {
            "call_id": call_id,
            "lead_id": lead_id,
            "tenant_id": tenant_id,
            "channel": "voice",
            "call_type": call_type,
            "phone_number": phone_number,
            "attempt_number": attempt_number,
            "status": "initiated",
            "agent_id": self.settings.retell_agent_id,
            "lead_data_snapshot": lead_data,
            "transcript": None,
            "call_analysis": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        await self.db.calls.insert_one(record)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_lead_id(call: RetellCallObject) -> str | None:
        """Extract lead_id injected via dynamic_variables or metadata."""
        if call.retell_llm_dynamic_variables:
            return call.retell_llm_dynamic_variables.get("lead_id")
        if call.metadata:
            return call.metadata.get("lead_id")
        return None
