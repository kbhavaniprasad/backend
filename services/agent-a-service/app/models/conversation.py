"""
Pydantic models for Conversation documents stored in MongoDB.

All models use Pydantic v2 syntax.  The top-level `Conversation` document
is serialised to/from MongoDB with an explicit `id` field that maps to the
MongoDB `_id` ObjectId (stored as a plain string for transport).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────


class ChannelEnum(str, Enum):
    """Engagement channel used for the conversation."""

    voice = "voice"
    whatsapp = "whatsapp"
    sms = "sms"
    chat = "chat"
    instagram = "instagram"


class ConversationStatus(str, Enum):
    """Lifecycle status of a conversation."""

    initiated = "initiated"
    active = "active"
    completed = "completed"
    failed = "failed"
    no_answer = "no_answer"


class MessageRole(str, Enum):
    """Speaker role within a conversation message."""

    user = "user"        # the lead / human
    assistant = "assistant"  # the AI agent
    system = "system"    # internal system messages


# ─────────────────────────────────────────────────────────────────────────────
# Sub-documents
# ─────────────────────────────────────────────────────────────────────────────


class Message(BaseModel):
    """A single turn in the conversation."""

    role: MessageRole = Field(..., description="Speaker role for this message.")
    content: str = Field(..., description="Text content of the message.")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the message was created.",
    )
    audio_url: str | None = Field(
        default=None,
        description="URL to the audio recording of this message (voice channels only).",
    )

    model_config = {"use_enum_values": True}


class ConversationMetrics(BaseModel):
    """Quantitative metrics collected during a conversation."""

    duration_seconds: float = Field(
        default=0.0,
        description="Total conversation duration in seconds.",
    )
    words_spoken_by_agent: int = Field(
        default=0,
        description="Approximate word count for all assistant messages.",
    )
    words_spoken_by_lead: int = Field(
        default=0,
        description="Approximate word count for all user messages.",
    )
    sentiment_score: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Overall sentiment of the lead's messages (-1 very negative → +1 very positive).",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Root document
# ─────────────────────────────────────────────────────────────────────────────


class Conversation(BaseModel):
    """
    Root MongoDB document representing one engagement conversation.

    The `id` field is the canonical document identifier.  When persisted to
    MongoDB it is stored under the `_id` key (handled by the database layer).
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique conversation identifier (UUID-4).",
    )
    tenant_id: str = Field(
        ...,
        description="Identifier of the tenant / business that owns this conversation.",
    )
    lead_id: str = Field(
        ...,
        description="Identifier of the lead being engaged.",
    )
    channel: ChannelEnum = Field(
        ...,
        description="Communication channel used for this conversation.",
    )
    status: ConversationStatus = Field(
        default=ConversationStatus.initiated,
        description="Current lifecycle status of the conversation.",
    )

    # ── Message history ─────────────────────────────────────────────────────
    messages: list[Message] = Field(
        default_factory=list,
        description="Ordered list of messages in this conversation.",
    )

    # ── Post-conversation artefacts ─────────────────────────────────────────
    transcript: str | None = Field(
        default=None,
        description="Full plain-text transcript of the conversation.",
    )
    summary: str | None = Field(
        default=None,
        description="AI-generated summary of the conversation.",
    )
    qualification_result: dict[str, Any] | None = Field(
        default=None,
        description="Output from LeadQualificationEngine.qualify_lead().",
    )

    # ── Meeting booking ──────────────────────────────────────────────────────
    meeting_booked: bool = Field(
        default=False,
        description="Whether a meeting was successfully booked during this conversation.",
    )
    meeting_details: dict[str, Any] | None = Field(
        default=None,
        description="Meeting metadata (date, time, type, calendar link) if booked.",
    )

    # ── Quality / performance metrics ────────────────────────────────────────
    metrics: ConversationMetrics | None = Field(
        default=None,
        description="Quantitative metrics for this conversation.",
    )

    # ── Versioning ───────────────────────────────────────────────────────────
    agent_version: str = Field(
        default="1.0.0",
        description="Semantic version of the agent codebase that handled this conversation.",
    )
    prompt_version: str = Field(
        default="v1",
        description="Identifier of the prompt template version used.",
    )

    # ── Timestamps ───────────────────────────────────────────────────────────
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the conversation document was created.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the last update to this document.",
    )
    ended_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the conversation was completed or failed.",
    )

    model_config = {"use_enum_values": True}

    # ── Helpers ──────────────────────────────────────────────────────────────

    def build_transcript(self) -> str:
        """
        Reconstruct a plain-text transcript from the message list.
        The transcript field is also updated on the instance.
        """
        lines: list[str] = []
        for msg in self.messages:
            role_label = msg.role.upper() if isinstance(msg.role, str) else msg.role.value.upper()
            lines.append(f"{role_label}: {msg.content}")
        self.transcript = "\n".join(lines)
        return self.transcript

    def word_count(self, role: MessageRole) -> int:
        """Return total word count for a given speaker role."""
        role_val = role.value if isinstance(role, MessageRole) else role
        return sum(
            len(m.content.split())
            for m in self.messages
            if (m.role.value if isinstance(m.role, MessageRole) else m.role) == role_val
        )


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response schemas (used by the HTTP routers)
# ─────────────────────────────────────────────────────────────────────────────


class StartConversationRequest(BaseModel):
    """Body for POST /api/v1/conversations/start."""

    tenant_id: str
    lead_id: str
    channel: ChannelEnum
    lead_data: dict[str, Any] = Field(default_factory=dict)


class SendMessageRequest(BaseModel):
    """Body for POST /api/v1/conversations/{conversation_id}/message."""

    content: str
    role: MessageRole = MessageRole.user


class ConversationListItem(BaseModel):
    """Lightweight summary returned when listing conversations."""

    id: str
    tenant_id: str
    lead_id: str
    channel: ChannelEnum
    status: ConversationStatus
    meeting_booked: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"use_enum_values": True}
