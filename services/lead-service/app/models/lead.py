"""
app/models/lead.py – SQLAlchemy ORM models for leads and lead status history.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ── Enumerations ──────────────────────────────────────────────────────────────


class LeadSource(str, enum.Enum):
    """Traffic / acquisition source for an inbound lead."""

    facebook_ads = "facebook_ads"
    google_ads = "google_ads"
    linkedin_ads = "linkedin_ads"
    website_form = "website_form"
    whatsapp = "whatsapp"
    instagram_dm = "instagram_dm"
    crm = "crm"
    manual = "manual"


class LeadStatus(str, enum.Enum):
    """Sales-pipeline status of a lead."""

    new = "new"
    contacted = "contacted"
    interested = "interested"
    follow_up_required = "follow_up_required"
    meeting_booked = "meeting_booked"
    not_interested = "not_interested"
    no_response = "no_response"
    qualified = "qualified"
    disqualified = "disqualified"


# ── Lead model ─────────────────────────────────────────────────────────────────


class Lead(Base):
    """
    Core lead record.

    Stores all demographic, attribution, and pipeline information for a
    single inbound lead across every acquisition channel.
    """

    __tablename__ = "leads"

    # ── Primary key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    # ── Multi-tenancy ─────────────────────────────────────────────────────────
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="Identifies the tenant / organisation that owns this lead.",
    )

    # ── External reference ────────────────────────────────────────────────────
    external_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="Platform-specific lead ID (e.g. Facebook leadgen_id).",
    )

    # ── Channel ───────────────────────────────────────────────────────────────
    source: Mapped[LeadSource] = mapped_column(
        SAEnum(LeadSource, name="leadsource"),
        nullable=False,
        index=True,
    )

    # ── Pipeline status ───────────────────────────────────────────────────────
    status: Mapped[LeadStatus] = mapped_column(
        SAEnum(LeadStatus, name="leadstatus"),
        nullable=False,
        default=LeadStatus.new,
        index=True,
    )

    # ── Contact information ───────────────────────────────────────────────────
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Geo ───────────────────────────────────────────────────────────────────
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Ad attribution ────────────────────────────────────────────────────────
    ad_campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ad_set_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ad_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── UTM attribution ───────────────────────────────────────────────────────
    utm_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Payload / notes ───────────────────────────────────────────────────────
    raw_data: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Original webhook / form payload stored verbatim for audit.",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Assignment ────────────────────────────────────────────────────────────
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="Agent / user who owns this lead in the CRM.",
    )

    # ── Soft delete ───────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    contacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of first contact attempt.",
    )
    qualified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when lead was marked as qualified.",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    status_history: Mapped[list["LeadStatusHistory"]] = relationship(
        "LeadStatusHistory",
        back_populates="lead",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="LeadStatusHistory.created_at.asc()",
    )

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_leads_tenant_status", "tenant_id", "status"),
        Index("ix_leads_tenant_source", "tenant_id", "source"),
        Index("ix_leads_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_leads_tenant_external",
            "tenant_id",
            "external_id",
            unique=True,
            postgresql_where="external_id IS NOT NULL",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Lead id={self.id} source={self.source.value} "
            f"status={self.status.value} name={self.first_name} {self.last_name}>"
        )


# ── LeadStatusHistory model ────────────────────────────────────────────────────


class LeadStatusHistory(Base):
    """
    Immutable audit trail of every pipeline-status change on a Lead.

    A new row is appended each time Lead.status transitions to a new value,
    recording who triggered the change and optionally a reason.
    """

    __tablename__ = "lead_status_history"

    # ── Primary key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Foreign key ───────────────────────────────────────────────────────────
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Transition ────────────────────────────────────────────────────────────
    old_status: Mapped[LeadStatus] = mapped_column(
        SAEnum(LeadStatus, name="leadstatus"),
        nullable=False,
    )
    new_status: Mapped[LeadStatus] = mapped_column(
        SAEnum(LeadStatus, name="leadstatus"),
        nullable=False,
    )

    # ── Actor ─────────────────────────────────────────────────────────────────
    changed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="UUID of the user or system actor that triggered the change.",
    )

    # ── Optional context ──────────────────────────────────────────────────────
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable reason for the status transition.",
    )

    # ── Timestamp ─────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    lead: Mapped["Lead"] = relationship("Lead", back_populates="status_history")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LeadStatusHistory lead={self.lead_id} "
            f"{self.old_status.value} → {self.new_status.value}>"
        )
