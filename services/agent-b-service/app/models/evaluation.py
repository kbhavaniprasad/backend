"""
Pydantic models for Agent B evaluation data stored in MongoDB.
Covers evaluation reports, mistakes, learnings, and related enumerations.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field
from bson import ObjectId


# ---------------------------------------------------------------------------
# Helper for MongoDB ObjectId serialisation
# ---------------------------------------------------------------------------

class PyObjectId(str):
    """Custom type that serialises MongoDB ObjectId to string."""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        if ObjectId.is_valid(str(v)):
            return str(v)
        raise ValueError(f"Invalid ObjectId: {v!r}")

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        return core_schema.no_info_plain_validator_function(cls.validate)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MistakeType(str, Enum):
    """Categories of mistakes Agent A can make during a conversation."""

    wrong_information = "wrong_information"
    missed_upsell = "missed_upsell"
    missed_qualification = "missed_qualification"
    hallucination = "hallucination"
    poor_tone = "poor_tone"
    incorrect_pricing = "incorrect_pricing"
    missed_objection_handling = "missed_objection_handling"
    too_long = "too_long"
    too_short = "too_short"
    other = "other"


class SeverityLevel(str, Enum):
    """Severity of a detected mistake or learning."""

    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class ImprovementStatus(str, Enum):
    """Track whether corrections derived from an evaluation have been applied."""

    pending = "pending"
    applied = "applied"
    failed = "failed"
    rolled_back = "rolled_back"


class LearningStatus(str, Enum):
    """Lifecycle status of an individual learning record."""

    pending = "pending"
    applied = "applied"
    verified = "verified"
    rolled_back = "rolled_back"


class SentimentTrend(str, Enum):
    """Overall sentiment trend observed in the conversation."""

    improving = "improving"
    stable = "stable"
    declining = "declining"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class Mistake(BaseModel):
    """A single mistake detected during evaluation of Agent A's response."""

    type: MistakeType = Field(..., description="Category of the mistake")
    description: str = Field(
        ...,
        description="Human-readable description of what went wrong",
        min_length=5,
        max_length=1000,
    )
    timestamp_in_call: float = Field(
        ...,
        description="Approximate second in the conversation when the mistake occurred",
        ge=0,
    )
    recommended_correction: str = Field(
        ...,
        description="Suggested correction or improved response",
        min_length=5,
        max_length=2000,
    )
    severity: SeverityLevel = Field(..., description="How critical this mistake is")
    confidence_score: float = Field(
        ...,
        description="Model confidence that this is genuinely a mistake (0–1)",
        ge=0.0,
        le=1.0,
    )


# ---------------------------------------------------------------------------
# Primary Documents
# ---------------------------------------------------------------------------

class EvaluationReport(BaseModel):
    """
    Full evaluation report produced by Agent B for a completed conversation.
    Stored in the 'evaluation_reports' MongoDB collection.
    """

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    tenant_id: str = Field(..., description="Tenant / organisation identifier")
    conversation_id: str = Field(..., description="ID of the evaluated conversation")
    lead_id: str = Field(..., description="CRM lead identifier")

    # Scoring
    overall_score: float = Field(
        ...,
        description="Overall quality score from 0 (worst) to 10 (best)",
        ge=0.0,
        le=10.0,
    )
    qualification_accuracy: float = Field(
        ...,
        description="How accurately Agent A qualified the lead (0–1)",
        ge=0.0,
        le=1.0,
    )

    # Detailed findings
    mistakes: List[Mistake] = Field(default_factory=list)
    strengths: List[str] = Field(
        default_factory=list,
        description="Things Agent A did particularly well",
    )
    missed_opportunities: List[str] = Field(
        default_factory=list,
        description="Opportunities Agent A did not capitalise on",
    )
    coaching_feedback: str = Field(
        ...,
        description="Narrative coaching feedback for the agent improvement system",
        min_length=10,
    )
    sentiment_trend: SentimentTrend = Field(
        ...,
        description="How the lead's sentiment evolved during the call",
    )

    # Lifecycle
    improvement_status: ImprovementStatus = Field(
        default=ImprovementStatus.pending,
        description="Whether corrections from this evaluation have been applied",
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
        "json_schema_extra": {
            "example": {
                "tenant_id": "tenant_abc",
                "conversation_id": "conv_123",
                "lead_id": "lead_456",
                "overall_score": 7.5,
                "qualification_accuracy": 0.85,
                "mistakes": [],
                "strengths": ["Good rapport building", "Clear pricing explanation"],
                "missed_opportunities": ["Did not attempt upsell on premium plan"],
                "coaching_feedback": "Agent performed well but missed an upsell window.",
                "sentiment_trend": "stable",
                "improvement_status": "pending",
            }
        },
    }


class Learning(BaseModel):
    """
    An actionable learning derived from an evaluation report.
    Once applied, the correction_prompt_snippet is injected into Agent A's prompt.
    Stored in the 'learnings' MongoDB collection.
    """

    id: Optional[PyObjectId] = Field(default=None, alias="_id")
    tenant_id: str = Field(..., description="Tenant / organisation identifier")
    source_evaluation_id: str = Field(
        ..., description="ID of the EvaluationReport that generated this learning"
    )

    # Classification
    category: str = Field(
        ...,
        description="High-level category e.g. 'pricing', 'objection_handling'",
        max_length=100,
    )
    title: str = Field(..., description="Short title for this learning", max_length=200)
    description: str = Field(
        ..., description="Detailed description of the lesson learned", max_length=2000
    )

    # Behaviour diff
    old_behavior: str = Field(
        ...,
        description="Description of the problematic behaviour observed",
        max_length=1000,
    )
    new_behavior: str = Field(
        ...,
        description="Description of the desired replacement behaviour",
        max_length=1000,
    )
    correction_prompt_snippet: str = Field(
        ...,
        description="Verbatim text to inject into Agent A's system prompt",
        max_length=2000,
    )

    # Confidence & severity
    confidence_score: float = Field(
        ...,
        description="Confidence that applying this learning will improve performance (0–1)",
        ge=0.0,
        le=1.0,
    )
    severity: SeverityLevel = Field(
        ..., description="Severity of the original mistake this corrects"
    )

    # Lifecycle
    status: LearningStatus = Field(default=LearningStatus.pending)
    applied_at: Optional[datetime] = Field(
        default=None, description="When the learning was applied to Agent A's prompt"
    )
    verified_at: Optional[datetime] = Field(
        default=None, description="When the improvement was verified in production"
    )
    rollback_reason: Optional[str] = Field(
        default=None,
        description="Reason for rollback, if status is rolled_back",
        max_length=500,
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
        "json_schema_extra": {
            "example": {
                "tenant_id": "tenant_abc",
                "source_evaluation_id": "eval_789",
                "category": "pricing",
                "title": "Correct pricing for Pro plan",
                "description": "Agent quoted wrong price for Pro tier.",
                "old_behavior": "Agent says Pro plan is $49/month.",
                "new_behavior": "Agent must quote Pro plan at $79/month.",
                "correction_prompt_snippet": "IMPORTANT: The Pro plan is priced at $79/month, not $49/month.",
                "confidence_score": 0.95,
                "severity": "critical",
                "status": "pending",
            }
        },
    }
