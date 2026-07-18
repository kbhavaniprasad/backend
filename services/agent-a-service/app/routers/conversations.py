"""
Conversations router — /api/v1/conversations

Endpoints:
  GET  /                               List conversations for a tenant (paginated)
  GET  /{conversation_id}              Get a single conversation with full transcript
  POST /{conversation_id}/message      Send a message (chat / text channels)
  GET  /{conversation_id}/summary      AI-generated conversation summary
  POST /start                          Manually start a conversation with a lead
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.conversation import (
    Conversation,
    ConversationListItem,
    ConversationStatus,
    Message,
    MessageRole,
    SendMessageRequest,
    StartConversationRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


# ─────────────────────────────────────────────────────────────────────────────
# Dependency helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_db(request: Request) -> AsyncIOMotorDatabase:  # type: ignore[type-arg]
    """Extract the shared Motor database from app state."""
    return request.app.state.db


def _get_conversation_handler(request: Request):  # type: ignore[return]
    """Extract the ConversationHandler from app state."""
    return request.app.state.conversation_handler


def _get_producer(request: Request):  # type: ignore[return]
    """Extract the EventProducer from app state."""
    return request.app.state.producer


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _get_conversation_or_404(
    conversation_id: str,
    db: AsyncIOMotorDatabase,  # type: ignore[type-arg]
) -> dict[str, Any]:
    """Fetch a conversation document from MongoDB or raise 404."""
    doc = await db.conversations.find_one({"id": conversation_id})
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )
    return doc


def _doc_to_conversation(doc: dict[str, Any]) -> Conversation:
    """Convert a raw MongoDB document dict to a Conversation model."""
    doc.pop("_id", None)  # strip ObjectId
    return Conversation.model_validate(doc)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=list[ConversationListItem],
    summary="List conversations for a tenant",
)
async def list_conversations(
    request: Request,
    tenant_id: Annotated[str, Query(description="Tenant identifier (required)")],
    status_filter: Annotated[
        ConversationStatus | None,
        Query(alias="status", description="Filter by conversation status"),
    ] = None,
    channel: Annotated[str | None, Query(description="Filter by channel")] = None,
    lead_id: Annotated[str | None, Query(description="Filter by lead ID")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Maximum results to return")] = 50,
    skip: Annotated[int, Query(ge=0, description="Number of records to skip")] = 0,
    db: AsyncIOMotorDatabase = Depends(_get_db),  # type: ignore[type-arg]
) -> list[ConversationListItem]:
    """
    Return a paginated list of conversations for the given tenant, with optional
    filters for status, channel, and lead.
    """
    query: dict[str, Any] = {"tenant_id": tenant_id}
    if status_filter:
        query["status"] = status_filter.value if hasattr(status_filter, "value") else status_filter
    if channel:
        query["channel"] = channel
    if lead_id:
        query["lead_id"] = lead_id

    cursor = (
        db.conversations.find(query, {"messages": 0})  # exclude message arrays for list view
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )

    docs = await cursor.to_list(length=limit)
    items: list[ConversationListItem] = []
    for doc in docs:
        doc.pop("_id", None)
        try:
            items.append(ConversationListItem.model_validate(doc))
        except Exception as exc:
            logger.warning("Skipping malformed conversation doc: %s", exc)

    return items


@router.get(
    "/{conversation_id}",
    response_model=Conversation,
    summary="Get a conversation with full transcript",
)
async def get_conversation(
    conversation_id: str,
    db: AsyncIOMotorDatabase = Depends(_get_db),  # type: ignore[type-arg]
) -> Conversation:
    """Return the full Conversation document including all messages."""
    doc = await _get_conversation_or_404(conversation_id, db)
    return _doc_to_conversation(doc)


@router.post(
    "/{conversation_id}/message",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Send a message in an existing conversation (chat channels)",
)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    request: Request,
    db: AsyncIOMotorDatabase = Depends(_get_db),  # type: ignore[type-arg]
) -> dict[str, Any]:
    """
    Add a user message to an existing conversation and return the AI response.
    Intended for chat, SMS, WhatsApp, and Instagram channels where messages
    arrive as discrete HTTP calls rather than a live audio stream.
    """
    doc = await _get_conversation_or_404(conversation_id, db)
    conversation = _doc_to_conversation(doc)

    if conversation.status in (ConversationStatus.completed, ConversationStatus.failed):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Conversation is already in terminal status '{conversation.status}'.",
        )

    handler = _get_conversation_handler(request)

    # Fetch lead data for richer system prompt context
    lead_data: dict[str, Any] = {}
    try:
        lead_svc_url = request.app.state.settings.lead_service_url
        async with request.app.state.http_client.stream(
            "GET", f"{lead_svc_url}/api/v1/leads/{conversation.lead_id}"
        ) as resp:
            if resp.status_code == 200:
                lead_data = resp.json()
    except Exception as exc:
        logger.warning("Could not fetch lead data (non-fatal): %s", exc)

    # Process the message through the AI pipeline
    ai_response = await handler.handle_message(
        conversation=conversation,
        new_message=body.content,
        tenant_id=conversation.tenant_id,
        lead_data=lead_data,
    )

    # Persist updated conversation
    update_doc = {
        "messages": [m.model_dump() for m in conversation.messages],
        "status": conversation.status,
        "meeting_booked": conversation.meeting_booked,
        "meeting_details": conversation.meeting_details,
        "updated_at": datetime.now(timezone.utc),
    }
    await db.conversations.update_one(
        {"id": conversation_id},
        {"$set": update_doc},
    )

    # If a meeting was just booked, publish the event
    if conversation.meeting_booked and conversation.meeting_details:
        try:
            producer = _get_producer(request)
            await producer.publish_meeting_booked(
                conversation_id=conversation_id,
                tenant_id=conversation.tenant_id,
                lead_id=conversation.lead_id,
                meeting_details=conversation.meeting_details,
            )
        except Exception as exc:
            logger.warning("Failed to publish meeting.booked event: %s", exc)

    return {
        "conversation_id": conversation_id,
        "response": ai_response,
        "meeting_booked": conversation.meeting_booked,
        "meeting_details": conversation.meeting_details,
    }


@router.get(
    "/{conversation_id}/summary",
    response_model=dict[str, Any],
    summary="Get AI-generated conversation summary",
)
async def get_summary(
    conversation_id: str,
    request: Request,
    regenerate: Annotated[
        bool,
        Query(description="Force re-generation even if a cached summary exists"),
    ] = False,
    db: AsyncIOMotorDatabase = Depends(_get_db),  # type: ignore[type-arg]
) -> dict[str, Any]:
    """
    Return a summary of the conversation.  The summary is generated on first
    request and cached on the Conversation document.  Pass ``?regenerate=true``
    to force a fresh generation.
    """
    doc = await _get_conversation_or_404(conversation_id, db)
    conversation = _doc_to_conversation(doc)

    if conversation.summary and not regenerate:
        return {
            "conversation_id": conversation_id,
            "summary": conversation.summary,
            "cached": True,
        }

    handler = _get_conversation_handler(request)
    summary = await handler.generate_summary(conversation)

    # Persist the summary
    await db.conversations.update_one(
        {"id": conversation_id},
        {"$set": {"summary": summary, "updated_at": datetime.now(timezone.utc)}},
    )

    return {
        "conversation_id": conversation_id,
        "summary": summary,
        "cached": False,
    }


@router.post(
    "/start",
    response_model=Conversation,
    status_code=status.HTTP_201_CREATED,
    summary="Manually start a conversation with a lead",
)
async def start_conversation(
    body: StartConversationRequest,
    request: Request,
    db: AsyncIOMotorDatabase = Depends(_get_db),  # type: ignore[type-arg]
) -> Conversation:
    """
    Manually trigger the agent to start a conversation with a specific lead.
    Useful for testing and for CRM-initiated outreach.
    """
    handler = _get_conversation_handler(request)

    try:
        conversation = await handler.process_new_lead(
            lead_data={"id": body.lead_id, **body.lead_data},
            tenant_id=body.tenant_id,
            channel=body.channel,
        )
    except Exception as exc:
        logger.error("Failed to start conversation: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start conversation: {exc}",
        )

    # Persist to MongoDB
    doc = conversation.model_dump()
    await db.conversations.insert_one(doc)
    logger.info(
        "Conversation started via API | id=%s tenant=%s lead=%s",
        conversation.id,
        body.tenant_id,
        body.lead_id,
    )

    return conversation
