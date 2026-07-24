"""
models.py — Pydantic request / response models
All API data shapes are defined here for type safety and validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Generic API envelope ──────────────────────────────────────────────────────

class APIResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Any] = None
    error: Optional[str] = None


# ── Voice call models ─────────────────────────────────────────────────────────

class StartCallRequest(BaseModel):
    """Body sent by the frontend when starting a voice call."""
    agent_id: Optional[str] = Field(
        default=None,
        description="Override the default agent ID from .env"
    )
    metadata: dict = Field(default_factory=dict)


class StopCallRequest(BaseModel):
    """Body sent by the frontend when a call ends."""
    call_id: str
    duration_seconds: Optional[int] = None
    transcript: Optional[str] = None


# ── Session history model ─────────────────────────────────────────────────────

class CallSession(BaseModel):
    id: int
    call_id: str
    agent_id: str
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    transcript: Optional[str] = None


# ── Lead Capture models ───────────────────────────────────────────────────────

class LeadCreate(BaseModel):
    name: str = Field(..., example="John Doe")
    email: Optional[str] = Field(default=None, example="john@example.com")
    phone: Optional[str] = Field(default=None, example="+1234567890")
    company: Optional[str] = Field(default=None, example="Acme Corp")
    requirement: Optional[str] = Field(default=None, example="Automated sales calls")
    source: str = Field(default="form", example="form") # 'instant' | 'form'
    agent_type: str = Field(default="voice", example="voice") # 'voice' | 'chat'


# ── Live Chat models ──────────────────────────────────────────────────────────

class ChatMessageRequest(BaseModel):
    session_id: str
    message: str
    user_info: Optional[dict] = Field(default_factory=dict)


class TriggerAgentRequest(BaseModel):
    lead_id: Optional[int] = None
    agent_type: str = "voice" # 'voice' | 'chat'
