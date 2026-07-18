"""
app/schemas/lead.py – Pydantic v2 request / response schemas for leads.

All schemas are strict where appropriate and use alias_generator / model_config
for clean JSON serialisation.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


# ── Enumerations ──────────────────────────────────────────────────────────────


class LeadSource(str, enum.Enum):
    """Acquisition channel / source of a lead."""

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


# ── Shared config ─────────────────────────────────────────────────────────────

_ORM_CONFIG = ConfigDict(from_attributes=True)


# ── Request schemas ───────────────────────────────────────────────────────────


class CreateLeadRequest(BaseModel):
    """Payload for creating a new lead (API or internal)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # Channel
    source: LeadSource

    # Contact
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr | None = Field(default=None)
    phone: str | None = Field(default=None, max_length=50)
    company: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)

    # Geo
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)

    # Ad attribution
    ad_campaign_id: str | None = Field(default=None, max_length=255)
    ad_set_id: str | None = Field(default=None, max_length=255)
    ad_id: str | None = Field(default=None, max_length=255)

    # UTM attribution
    utm_source: str | None = Field(default=None, max_length=255)
    utm_medium: str | None = Field(default=None, max_length=255)
    utm_campaign: str | None = Field(default=None, max_length=255)

    # External reference (platform-specific ID)
    external_id: str | None = Field(default=None, max_length=255)

    # Raw payload storage
    raw_data: dict[str, Any] | None = Field(
        default=None,
        description="Original webhook or form payload for audit purposes.",
    )

    # Assignment
    assigned_agent_id: uuid.UUID | None = Field(default=None)

    @model_validator(mode="after")
    def require_phone_or_email(self) -> "CreateLeadRequest":
        """At least one contact method must be provided."""
        if not self.email and not self.phone:
            raise ValueError("At least one of 'email' or 'phone' must be provided.")
        return self


class UpdateLeadStatusRequest(BaseModel):
    """Payload for transitioning a lead's pipeline status."""

    status: LeadStatus
    reason: str | None = Field(
        default=None,
        max_length=1000,
        description="Human-readable reason for the status change.",
    )


class UpdateLeadRequest(BaseModel):
    """Partial update schema for non-status lead fields."""

    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    company: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    notes: str | None = None
    assigned_agent_id: uuid.UUID | None = None


class BulkCreateLeadRequest(BaseModel):
    """Payload for bulk-importing leads (e.g. CRM import)."""

    leads: list[CreateLeadRequest] = Field(
        ...,
        min_length=1,
        description="List of leads to import. Max 500 per request.",
    )
    source: LeadSource = Field(
        default=LeadSource.crm,
        description="Override source for all leads in this batch.",
    )


# ── Response schemas ──────────────────────────────────────────────────────────


class LeadStatusHistoryResponse(BaseModel):
    """Single status-change audit entry."""

    model_config = _ORM_CONFIG

    id: uuid.UUID
    lead_id: uuid.UUID
    old_status: LeadStatus
    new_status: LeadStatus
    changed_by: uuid.UUID
    reason: str | None
    created_at: datetime


class LeadResponse(BaseModel):
    """Full lead representation returned by the API."""

    model_config = _ORM_CONFIG

    # Identity
    id: uuid.UUID
    tenant_id: uuid.UUID
    external_id: str | None

    # Channel & pipeline
    source: LeadSource
    status: LeadStatus

    # Contact
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    company: str | None
    job_title: str | None
    city: str | None
    country: str | None

    # Attribution
    ad_campaign_id: str | None
    ad_set_id: str | None
    ad_id: str | None
    utm_source: str | None
    utm_medium: str | None
    utm_campaign: str | None

    # Payload / notes
    raw_data: dict[str, Any] | None
    notes: str | None

    # Assignment
    assigned_agent_id: uuid.UUID | None

    # Flags & timestamps
    is_active: bool
    created_at: datetime
    updated_at: datetime
    contacted_at: datetime | None
    qualified_at: datetime | None


class LeadListResponse(BaseModel):
    """Paginated list of leads."""

    items: list[LeadResponse]
    total: int = Field(..., description="Total number of matching records.")
    page: int = Field(..., description="Current page number (1-indexed).")
    page_size: int = Field(..., description="Number of items per page.")
    pages: int = Field(..., description="Total number of pages.")


class LeadStatsSummary(BaseModel):
    """Per-status lead counts for a given tenant."""

    tenant_id: uuid.UUID
    counts: dict[str, int] = Field(
        ...,
        description="Mapping of LeadStatus value → count of leads in that status.",
    )
    total: int


class BulkCreateLeadResponse(BaseModel):
    """Result of a bulk lead import operation."""

    created: int = Field(..., description="Number of leads successfully created.")
    failed: int = Field(..., description="Number of leads that failed to import.")
    errors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Details of individual failures, including index and reason.",
    )
    lead_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="UUIDs of successfully created leads.",
    )


# ── Webhook payload schemas ───────────────────────────────────────────────────


class WebhookFacebookEntry(BaseModel):
    """Single entry within a Facebook webhook change object."""

    leadgen_id: str = Field(..., description="Facebook Leadgen form submission ID.")
    page_id: str
    form_id: str
    ad_id: str | None = None
    ad_campaign_id: str | None = None
    ad_group_id: str | None = None
    created_time: int | None = None


class WebhookFacebookChange(BaseModel):
    """Facebook webhook 'changes' object."""

    field: str
    value: WebhookFacebookEntry


class WebhookFacebookLead(BaseModel):
    """
    Top-level Facebook Lead Ads webhook payload.

    Reference: https://developers.facebook.com/docs/marketing-api/guides/lead-ads/retrieving/
    """

    object: str = Field(..., description="Should be 'page' for lead webhooks.")
    entry: list[dict[str, Any]] = Field(
        ...,
        description="List of changed page entries.",
    )


class WebhookGoogleLead(BaseModel):
    """Google Ads lead form submission webhook payload."""

    lead_id: str | None = None
    campaign_id: str | None = None
    ad_group_id: str | None = None
    creative_id: str | None = None
    google_key: str | None = None
    user_column_data: list[dict[str, str]] = Field(default_factory=list)
    api_version: str | None = None


class WebhookLinkedInLead(BaseModel):
    """LinkedIn Lead Gen form submission webhook payload."""

    owner: str | None = None
    form_name: str | None = None
    form_response: dict[str, Any] = Field(default_factory=dict)
    submission_id: str | None = None
    campaign_id: str | None = None
    creative_id: str | None = None


class WebhookWebsiteForm(BaseModel):
    """Generic website contact / inquiry form submission."""

    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    company: str | None = Field(default=None, max_length=255)
    message: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None

    # Honeypot / bot protection
    website: str | None = Field(
        default=None,
        description="Honeypot field – must be empty for real submissions.",
    )


class WebhookWhatsAppMessage(BaseModel):
    """Simplified WhatsApp Cloud API incoming-message webhook payload."""

    object: str
    entry: list[dict[str, Any]] = Field(default_factory=list)


class WebhookCRMPayload(BaseModel):
    """Generic CRM webhook payload (Salesforce / HubSpot / Zoho)."""

    event_type: str | None = None
    record_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
