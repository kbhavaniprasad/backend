"""
alembic/versions/0001_initial_lead_tables.py

Initial migration: creates the ``leads`` and ``lead_status_history`` tables
along with all enums, indexes, and foreign keys.

Revision ID: 0001
Revises: –
Create Date: 2026-07-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

# Alembic revision identifiers
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enums ──────────────────────────────────────────────────────────────────
    leadsource = sa.Enum(
        "facebook_ads",
        "google_ads",
        "linkedin_ads",
        "website_form",
        "whatsapp",
        "instagram_dm",
        "crm",
        "manual",
        name="leadsource",
    )
    leadstatus = sa.Enum(
        "new",
        "contacted",
        "interested",
        "follow_up_required",
        "meeting_booked",
        "not_interested",
        "no_response",
        "qualified",
        "disqualified",
        name="leadstatus",
    )
    leadsource.create(op.get_bind(), checkfirst=True)
    leadstatus.create(op.get_bind(), checkfirst=True)

    # ── leads table ────────────────────────────────────────────────────────────
    op.create_table(
        "leads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("source", leadsource, nullable=False),
        sa.Column("status", leadstatus, nullable=False, server_default="new"),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("job_title", sa.String(255), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("ad_campaign_id", sa.String(255), nullable=True),
        sa.Column("ad_set_id", sa.String(255), nullable=True),
        sa.Column("ad_id", sa.String(255), nullable=True),
        sa.Column("utm_source", sa.String(255), nullable=True),
        sa.Column("utm_medium", sa.String(255), nullable=True),
        sa.Column("utm_campaign", sa.String(255), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("assigned_agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Simple indexes
    op.create_index("ix_leads_id", "leads", ["id"])
    op.create_index("ix_leads_tenant_id", "leads", ["tenant_id"])
    op.create_index("ix_leads_external_id", "leads", ["external_id"])
    op.create_index("ix_leads_source", "leads", ["source"])
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_email", "leads", ["email"])
    op.create_index("ix_leads_phone", "leads", ["phone"])
    op.create_index("ix_leads_assigned_agent_id", "leads", ["assigned_agent_id"])

    # Composite indexes
    op.create_index("ix_leads_tenant_status", "leads", ["tenant_id", "status"])
    op.create_index("ix_leads_tenant_source", "leads", ["tenant_id", "source"])
    op.create_index("ix_leads_tenant_created", "leads", ["tenant_id", "created_at"])

    # Partial unique index: one external_id per tenant (where external_id IS NOT NULL)
    op.create_index(
        "ix_leads_tenant_external",
        "leads",
        ["tenant_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    # ── lead_status_history table ──────────────────────────────────────────────
    op.create_table(
        "lead_status_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("old_status", leadstatus, nullable=False),
        sa.Column("new_status", leadstatus, nullable=False),
        sa.Column("changed_by", UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_lead_status_history_lead_id", "lead_status_history", ["lead_id"]
    )


def downgrade() -> None:
    op.drop_table("lead_status_history")

    op.drop_index("ix_leads_tenant_external", table_name="leads")
    op.drop_index("ix_leads_tenant_created", table_name="leads")
    op.drop_index("ix_leads_tenant_source", table_name="leads")
    op.drop_index("ix_leads_tenant_status", table_name="leads")
    op.drop_index("ix_leads_assigned_agent_id", table_name="leads")
    op.drop_index("ix_leads_phone", table_name="leads")
    op.drop_index("ix_leads_email", table_name="leads")
    op.drop_index("ix_leads_status", table_name="leads")
    op.drop_index("ix_leads_source", table_name="leads")
    op.drop_index("ix_leads_external_id", table_name="leads")
    op.drop_index("ix_leads_tenant_id", table_name="leads")
    op.drop_index("ix_leads_id", table_name="leads")
    op.drop_table("leads")

    sa.Enum(name="leadstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="leadsource").drop(op.get_bind(), checkfirst=True)
