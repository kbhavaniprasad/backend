"""
Shared Kafka Event Schemas
===========================
Canonical definitions for all platform-wide Kafka events.
All services must conform to these schemas when publishing/consuming.

Topics:
  lead.created              → Voice/messaging service triggers contact
  lead.status_changed       → Lead status updates
  call.initiated            → Voice service started a call
  call.started              → Retell confirmed call connected
  call.ended                → Call disconnected (transcript ready)
  conversation.completed    → Full call analyzed → triggers Agent B
  lead.messaging_contact    → Route to WhatsApp/Instagram messaging
  evaluation.completed      → Agent B evaluation done
  learning.applied          → Agent B pushed update to Agent A
  report.generated          → Business performance report ready
  meeting.booked            → Meeting successfully scheduled
  dashboard.update          → Real-time dashboard notification
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Base ──────────────────────────────────────────────────────────────────────

class BaseEvent(BaseModel):
    event: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    tenant_id: str
    lead_id: Optional[str] = None


# ── Lead Events ───────────────────────────────────────────────────────────────

class LeadCreatedEvent(BaseEvent):
    """Topic: lead.created — published by lead-service on new lead ingestion."""
    event: str = "lead.created"
    source: str          # facebook_ads, google_ads, etc.
    phone: Optional[str] = None
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None


class LeadStatusChangedEvent(BaseEvent):
    """Topic: lead.status_changed — published when lead status changes."""
    event: str = "lead.status_changed"
    old_status: str
    new_status: str
    changed_by: Optional[str] = None  # user_id or 'ai'
    reason: Optional[str] = None


# ── Call Events ───────────────────────────────────────────────────────────────

class CallInitiatedEvent(BaseEvent):
    """Topic: call.initiated — published just before Retell API call."""
    event: str = "call.initiated"
    channel: str = "voice"


class CallStartedEvent(BaseEvent):
    """Topic: call.started — Retell confirmed call answered."""
    event: str = "call.started"
    call_id: str
    channel: str = "voice"


class CallEndedEvent(BaseEvent):
    """Topic: call.ended — Call disconnected."""
    event: str = "call.ended"
    call_id: str
    status: str          # completed / no_answer / failed
    duration_seconds: float = 0.0
    disconnection_reason: Optional[str] = None


# ── Conversation Completed (Primary Agent B Trigger) ──────────────────────────

class ConversationCompletedEvent(BaseEvent):
    """
    Topic: conversation.completed
    Published by voice-service after Retell's call_analyzed webhook.
    This is the PRIMARY trigger for Agent B's evaluation pipeline.
    """
    event: str = "conversation.completed"
    call_id: str
    channel: str                           # voice / whatsapp / chat
    transcript: Optional[str] = None       # Full conversation text
    transcript_object: list[dict] = []     # Structured utterances
    call_summary: Optional[str] = None     # Retell AI generated summary
    user_sentiment: Optional[str] = None   # Positive/Negative/Neutral
    agent_sentiment: Optional[str] = None
    call_successful: Optional[bool] = None
    duration_seconds: float = 0.0
    recording_url: Optional[str] = None
    retell_agent_id: Optional[str] = None


# ── Agent B Events ────────────────────────────────────────────────────────────

class EvaluationCompletedEvent(BaseEvent):
    """Topic: evaluation.completed — Agent B finished evaluating a conversation."""
    event: str = "evaluation.completed"
    evaluation_id: str
    call_id: str
    overall_score: float       # 0.0 – 10.0
    mistake_count: int
    severity_distribution: dict[str, int]   # {critical: 2, high: 1, ...}
    improvement_status: str    # pending / applied


class LearningAppliedEvent(BaseEvent):
    """Topic: learning.applied — Agent B pushed a learning update to Agent A."""
    event: str = "learning.applied"
    learning_id: str
    category: str
    confidence_score: float
    severity: str
    retell_updated: bool       # Whether Retell agent prompt was updated


# ── Dashboard Events ──────────────────────────────────────────────────────────

class DashboardUpdateEvent(BaseEvent):
    """Topic: dashboard.update — Real-time dashboard push via WebSocket."""
    event: str = "dashboard.update"
    update_type: str           # new_lead / call_completed / meeting_booked / learning_applied
    data: dict[str, Any] = {}


# ── Meeting Events ────────────────────────────────────────────────────────────

class MeetingBookedEvent(BaseEvent):
    """Topic: meeting.booked — Meeting scheduled by Agent A."""
    event: str = "meeting.booked"
    meeting_id: str
    starts_at: str
    duration_minutes: int
    calendar_type: str
    ai_booked: bool = True
