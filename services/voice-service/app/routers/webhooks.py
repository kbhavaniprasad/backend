"""
Retell AI Webhook Router
=========================
Handles all incoming webhook events from Retell AI.

Retell sends POST requests to this endpoint for every call lifecycle event:
  - call_started   → Lead marked as 'contacted'
  - call_ended     → Transcript saved, retry scheduling
  - call_analyzed  → Full analysis → triggers Agent B evaluation pipeline

Security: HMAC-SHA256 signature verification (when RETELL_WEBHOOK_SECRET is set).
"""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.config import get_settings
from app.kafka.producer import KafkaProducerService
from app.retell.client import RetellClient
from app.retell.models import RetellCallAnalyzedEvent, RetellCallEndedEvent, RetellCallStartedEvent, RetellWebhookEvent
from app.retell.orchestrator import CallOrchestrator

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


async def _get_orchestrator(request: Request) -> CallOrchestrator:
    return CallOrchestrator(
        retell_client=request.app.state.retell,
        redis=request.app.state.redis,
        db=request.app.state.db,
        settings=settings,
    )


@router.post(
    "/retell",
    status_code=status.HTTP_200_OK,
    summary="Retell AI Webhook Receiver",
    description="Receives all call lifecycle events from Retell AI platform.",
)
async def retell_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Central Retell AI webhook handler.

    Retell sends events here for every call lifecycle stage.
    We return 200 immediately and process in background to meet Retell's
    5-second response timeout requirement.
    """
    body = await request.body()

    # ── Signature Verification ────────────────────────────────────────────────
    retell: RetellClient = request.app.state.retell
    signature = request.headers.get("X-Retell-Signature", "")
    if not retell.verify_webhook_signature(body, signature):
        logger.warning("Invalid Retell webhook signature")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # ── Parse Event ───────────────────────────────────────────────────────────
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("event")
    if not event_type:
        raise HTTPException(status_code=400, detail="Missing 'event' field")

    kafka_producer: KafkaProducerService = request.app.state.kafka_producer
    orchestrator = await _get_orchestrator(request)

    # ── Route by Event Type ───────────────────────────────────────────────────

    if event_type == "call_started":
        event = RetellCallStartedEvent(**payload)
        background_tasks.add_task(_handle_call_started, orchestrator, kafka_producer, event)

    elif event_type == "call_ended":
        event = RetellCallEndedEvent(**payload)
        background_tasks.add_task(_handle_call_ended, orchestrator, kafka_producer, event)

    elif event_type == "call_analyzed":
        event = RetellCallAnalyzedEvent(**payload)
        background_tasks.add_task(_handle_call_analyzed, orchestrator, kafka_producer, event)

    else:
        logger.info("Received unknown Retell event type: %s", event_type)

    # Always return 200 immediately (Retell requires < 5s response)
    return {"received": True, "event": event_type}


# ── Background Task Handlers ──────────────────────────────────────────────────

async def _handle_call_started(
    orchestrator: CallOrchestrator,
    kafka_producer: KafkaProducerService,
    event: RetellCallStartedEvent,
) -> None:
    """Handle call_started event — update lead status to 'contacted'."""
    try:
        await orchestrator.handle_call_started(event.call)

        lead_id = orchestrator._extract_lead_id(event.call)
        tenant_id = event.call.metadata.get("tenant_id") if event.call.metadata else None

        # Publish event so lead-service updates status to 'contacted'
        await kafka_producer.publish(
            topic="call.started",
            key=event.call.call_id,
            value={
                "event": "call.started",
                "call_id": event.call.call_id,
                "lead_id": lead_id,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    except Exception as exc:
        logger.exception("Error handling call_started: %s", exc)


async def _handle_call_ended(
    orchestrator: CallOrchestrator,
    kafka_producer: KafkaProducerService,
    event: RetellCallEndedEvent,
) -> None:
    """
    Handle call_ended event.
    Saves transcript to MongoDB and publishes conversation.completed for Agent B.
    """
    try:
        conversation_data = await orchestrator.handle_call_ended(event.call)

        lead_id = orchestrator._extract_lead_id(event.call)
        tenant_id = event.call.metadata.get("tenant_id") if event.call.metadata else None

        # Publish call.ended so lead-service can update status
        await kafka_producer.publish(
            topic="call.ended",
            key=event.call.call_id,
            value={
                "event": "call.ended",
                "call_id": event.call.call_id,
                "lead_id": lead_id,
                "tenant_id": tenant_id,
                "status": conversation_data.get("status"),
                "duration_seconds": conversation_data.get("duration_seconds"),
                "disconnection_reason": conversation_data.get("disconnection_reason"),
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        # Note: We wait for call_analyzed before publishing conversation.completed
        # so Agent B gets the full analysis with the transcript
        logger.info("Call ended processed", extra={"call_id": event.call.call_id})

    except Exception as exc:
        logger.exception("Error handling call_ended: %s", exc)


async def _handle_call_analyzed(
    orchestrator: CallOrchestrator,
    kafka_producer: KafkaProducerService,
    event: RetellCallAnalyzedEvent,
) -> None:
    """
    Handle call_analyzed event — the primary trigger for Agent B.

    This is fired after Retell's AI post-call analysis completes.
    It contains the full transcript + summary + sentiment.
    We publish 'conversation.completed' which Agent B consumes to:
      1. Evaluate the call quality
      2. Detect mistakes and missed opportunities
      3. Generate learnings and improvements
      4. Update the Retell agent's prompt if needed
    """
    try:
        full_call_doc = await orchestrator.handle_call_analyzed(event.call)

        lead_id = orchestrator._extract_lead_id(event.call)
        tenant_id = event.call.metadata.get("tenant_id") if event.call.metadata else None
        call_analysis = event.call.call_analysis or {}

        # Publish conversation.completed → Agent B evaluates
        await kafka_producer.publish(
            topic="conversation.completed",
            key=event.call.call_id,
            value={
                "event": "conversation.completed",
                "call_id": event.call.call_id,
                "lead_id": lead_id,
                "tenant_id": tenant_id,
                "channel": "voice",
                "transcript": event.call.transcript,
                "transcript_object": (
                    [u.model_dump() for u in event.call.transcript_object]
                    if event.call.transcript_object
                    else []
                ),
                "call_summary": call_analysis.get("call_summary"),
                "user_sentiment": call_analysis.get("user_sentiment"),
                "agent_sentiment": call_analysis.get("agent_sentiment"),
                "call_successful": call_analysis.get("call_successful"),
                "duration_seconds": (event.call.duration_ms or 0) / 1000,
                "recording_url": event.call.recording_url,
                "agent_id": event.call.agent_id,
                "retell_agent_id": settings.retell_agent_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        logger.info(
            "Call analyzed → conversation.completed published for Agent B",
            extra={
                "call_id": event.call.call_id,
                "lead_id": lead_id,
                "successful": call_analysis.get("call_successful"),
            },
        )

    except Exception as exc:
        logger.exception("Error handling call_analyzed: %s", exc)
