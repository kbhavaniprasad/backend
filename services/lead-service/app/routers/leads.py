"""
app/routers/leads.py – Lead CRUD and pipeline management endpoints.

Prefix  : /api/v1/leads
Auth    : Bearer JWT (all routes)
Events  : Kafka events published on create / status change
"""

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.lead import Lead, LeadSource, LeadStatus, LeadStatusHistory
from app.schemas.lead import (
    BulkCreateLeadRequest,
    BulkCreateLeadResponse,
    CreateLeadRequest,
    LeadListResponse,
    LeadResponse,
    LeadStatsSummary,
    LeadStatusHistoryResponse,
    UpdateLeadStatusRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/leads",
    tags=["Leads"],
)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_kafka(request: Request):
    """Retrieve the Kafka producer from app state."""
    return request.app.state.kafka_producer


def _require_tenant(request: Request) -> uuid.UUID:
    """
    Extract tenant_id from the validated JWT claims stored in request.state
    by a JWT middleware / dependency.  Falls back to a query param for
    service-to-service calls in development.

    In production, the JWT middleware should populate request.state.tenant_id.
    """
    tenant_id: uuid.UUID | None = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        # Fallback: accept X-Tenant-ID header (useful during development)
        raw = request.headers.get("X-Tenant-ID")
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing tenant context.",
            )
        try:
            tenant_id = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Tenant-ID header.",
            )
    return tenant_id


def _require_actor(request: Request) -> uuid.UUID:
    """Return the UUID of the authenticated user (actor)."""
    actor_id: uuid.UUID | None = getattr(request.state, "user_id", None)
    if actor_id is None:
        raw = request.headers.get("X-User-ID")
        if not raw:
            return uuid.UUID("00000000-0000-0000-0000-000000000000")  # system
        try:
            return uuid.UUID(raw)
        except ValueError:
            return uuid.UUID("00000000-0000-0000-0000-000000000000")
    return actor_id


# ── Helper: fetch or 404 ──────────────────────────────────────────────────────

async def _get_lead_or_404(
    lead_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> Lead:
    result = await db.execute(
        select(Lead).where(
            Lead.id == lead_id,
            Lead.tenant_id == tenant_id,
            Lead.is_active.is_(True),
        )
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found.",
        )
    return lead


# ── POST / – Create lead ──────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new lead",
)
async def create_lead(
    payload: CreateLeadRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LeadResponse:
    """
    Create a new lead record for the authenticated tenant.

    - Validates that at least one contact method (email or phone) is provided.
    - Publishes a ``lead.created`` Kafka event on success.
    - Returns HTTP 409 if a lead with the same ``external_id`` already exists
      for the tenant.
    """
    tenant_id = _require_tenant(request)

    # Duplicate check by external_id
    if payload.external_id:
        dup = await db.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.external_id == payload.external_id,
                Lead.is_active.is_(True),
            )
        )
        if dup.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Lead with external_id '{payload.external_id}' already exists.",
            )

    lead = Lead(
        tenant_id=tenant_id,
        source=LeadSource(payload.source.value),
        status=LeadStatus.new,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        phone=payload.phone,
        company=payload.company,
        job_title=payload.job_title,
        city=payload.city,
        country=payload.country,
        ad_campaign_id=payload.ad_campaign_id,
        ad_set_id=payload.ad_set_id,
        ad_id=payload.ad_id,
        utm_source=payload.utm_source,
        utm_medium=payload.utm_medium,
        utm_campaign=payload.utm_campaign,
        external_id=payload.external_id,
        raw_data=payload.raw_data,
        assigned_agent_id=payload.assigned_agent_id,
    )
    db.add(lead)
    await db.flush()   # get the generated UUID before commit
    await db.refresh(lead)

    logger.info("Lead created. lead_id=%s tenant_id=%s", lead.id, tenant_id)

    # Publish Kafka event (non-blocking; errors are logged, not raised)
    kafka = _get_kafka(request)
    if kafka:
        await kafka.publish_lead_created(
            lead_id=lead.id,
            tenant_id=tenant_id,
            lead_data={
                "source": lead.source.value,
                "phone": lead.phone,
                "email": lead.email,
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "assigned_agent_id": lead.assigned_agent_id,
                "ad_campaign_id": lead.ad_campaign_id,
                "utm_source": lead.utm_source,
                "utm_medium": lead.utm_medium,
                "utm_campaign": lead.utm_campaign,
            },
        )

    return LeadResponse.model_validate(lead)


# ── GET / – List leads ────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=LeadListResponse,
    summary="List leads with optional filters",
)
async def list_leads(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status_filter: Annotated[
        LeadStatus | None,
        Query(alias="status", description="Filter by pipeline status."),
    ] = None,
    source_filter: Annotated[
        LeadSource | None,
        Query(alias="source", description="Filter by acquisition source."),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Full-text search across first_name, last_name, email, phone."),
    ] = None,
    assigned_agent_id: Annotated[
        uuid.UUID | None,
        Query(description="Filter by assigned agent UUID."),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed).")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, description="Items per page.")
    ] = 20,
) -> LeadListResponse:
    """Return a paginated, filtered list of leads for the current tenant."""
    tenant_id = _require_tenant(request)

    base_query = select(Lead).where(
        Lead.tenant_id == tenant_id,
        Lead.is_active.is_(True),
    )

    if status_filter:
        base_query = base_query.where(Lead.status == LeadStatus(status_filter.value))
    if source_filter:
        base_query = base_query.where(Lead.source == LeadSource(source_filter.value))
    if assigned_agent_id:
        base_query = base_query.where(Lead.assigned_agent_id == assigned_agent_id)
    if search:
        like = f"%{search}%"
        from sqlalchemy import or_
        base_query = base_query.where(
            or_(
                Lead.first_name.ilike(like),
                Lead.last_name.ilike(like),
                Lead.email.ilike(like),
                Lead.phone.ilike(like),
            )
        )

    # Total count
    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar_one()

    # Paginated results
    offset = (page - 1) * page_size
    result = await db.execute(
        base_query.order_by(Lead.created_at.desc()).offset(offset).limit(page_size)
    )
    leads = result.scalars().all()

    return LeadListResponse(
        items=[LeadResponse.model_validate(l) for l in leads],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


# ── GET /stats/summary ────────────────────────────────────────────────────────

@router.get(
    "/stats/summary",
    response_model=LeadStatsSummary,
    summary="Lead count grouped by status for the current tenant",
)
async def lead_stats_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LeadStatsSummary:
    """
    Return the number of active leads in each pipeline status for the
    authenticated tenant.  Useful for dashboard KPI cards.
    """
    tenant_id = _require_tenant(request)

    result = await db.execute(
        select(Lead.status, func.count(Lead.id).label("cnt"))
        .where(Lead.tenant_id == tenant_id, Lead.is_active.is_(True))
        .group_by(Lead.status)
    )
    rows = result.all()

    counts: dict[str, int] = {s.value: 0 for s in LeadStatus}
    for row in rows:
        counts[row.status.value] = row.cnt

    return LeadStatsSummary(
        tenant_id=tenant_id,
        counts=counts,
        total=sum(counts.values()),
    )


# ── POST /bulk – Bulk create ──────────────────────────────────────────────────

@router.post(
    "/bulk",
    response_model=BulkCreateLeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk-create leads (CRM import)",
)
async def bulk_create_leads(
    payload: BulkCreateLeadRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BulkCreateLeadResponse:
    """
    Import up to 500 leads in a single request.

    Each lead is processed individually so that one failure does not abort
    the entire batch.  Returns a summary of successes and failures.
    """
    from app.config import get_settings
    settings = get_settings()

    tenant_id = _require_tenant(request)
    kafka = _get_kafka(request)

    if len(payload.leads) > settings.bulk_import_max_records:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {settings.bulk_import_max_records} leads per bulk request.",
        )

    created_ids: list[uuid.UUID] = []
    errors: list[dict] = []

    for idx, lead_req in enumerate(payload.leads):
        try:
            lead = Lead(
                tenant_id=tenant_id,
                source=LeadSource(payload.source.value),
                status=LeadStatus.new,
                first_name=lead_req.first_name,
                last_name=lead_req.last_name,
                email=lead_req.email,
                phone=lead_req.phone,
                company=lead_req.company,
                job_title=lead_req.job_title,
                city=lead_req.city,
                country=lead_req.country,
                ad_campaign_id=lead_req.ad_campaign_id,
                ad_set_id=lead_req.ad_set_id,
                ad_id=lead_req.ad_id,
                utm_source=lead_req.utm_source,
                utm_medium=lead_req.utm_medium,
                utm_campaign=lead_req.utm_campaign,
                external_id=lead_req.external_id,
                raw_data=lead_req.raw_data,
                assigned_agent_id=lead_req.assigned_agent_id,
            )
            db.add(lead)
            await db.flush()
            created_ids.append(lead.id)

            if kafka:
                await kafka.publish_lead_created(
                    lead_id=lead.id,
                    tenant_id=tenant_id,
                    lead_data={
                        "source": lead.source.value,
                        "phone": lead.phone,
                        "email": lead.email,
                        "first_name": lead.first_name,
                        "last_name": lead.last_name,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bulk import: lead[%d] failed – %s", idx, exc)
            errors.append({"index": idx, "reason": str(exc)})
            await db.rollback()

    logger.info(
        "Bulk lead import: created=%d failed=%d tenant_id=%s",
        len(created_ids),
        len(errors),
        tenant_id,
    )

    return BulkCreateLeadResponse(
        created=len(created_ids),
        failed=len(errors),
        errors=errors,
        lead_ids=created_ids,
    )


# ── GET /{lead_id} – Get single lead ─────────────────────────────────────────

@router.get(
    "/{lead_id}",
    response_model=LeadResponse,
    summary="Retrieve a single lead by ID",
)
async def get_lead(
    lead_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LeadResponse:
    """Return a single lead by its UUID, scoped to the current tenant."""
    tenant_id = _require_tenant(request)
    lead = await _get_lead_or_404(lead_id, tenant_id, db)
    return LeadResponse.model_validate(lead)


# ── PATCH /{lead_id}/status – Update status ───────────────────────────────────

@router.patch(
    "/{lead_id}/status",
    response_model=LeadResponse,
    summary="Transition a lead's pipeline status",
)
async def update_lead_status(
    lead_id: uuid.UUID,
    payload: UpdateLeadStatusRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LeadResponse:
    """
    Transition a lead to a new pipeline status.

    - Rejects no-op transitions (same status → same status).
    - Records an immutable entry in ``lead_status_history``.
    - Publishes a ``lead.status_changed`` Kafka event.
    - Updates ``contacted_at`` / ``qualified_at`` timestamps automatically.
    """
    tenant_id = _require_tenant(request)
    actor_id = _require_actor(request)
    lead = await _get_lead_or_404(lead_id, tenant_id, db)

    old_status = lead.status
    new_status = LeadStatus(payload.status.value)

    if old_status == new_status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lead is already in status '{new_status.value}'.",
        )

    # Update lead
    lead.status = new_status
    lead.updated_at = datetime.now(timezone.utc)

    if new_status == LeadStatus.contacted and lead.contacted_at is None:
        lead.contacted_at = datetime.now(timezone.utc)
    if new_status == LeadStatus.qualified and lead.qualified_at is None:
        lead.qualified_at = datetime.now(timezone.utc)

    # Record history
    history_entry = LeadStatusHistory(
        lead_id=lead.id,
        old_status=old_status,
        new_status=new_status,
        changed_by=actor_id,
        reason=payload.reason,
    )
    db.add(history_entry)
    await db.flush()
    await db.refresh(lead)

    logger.info(
        "Lead status updated. lead_id=%s %s → %s actor=%s",
        lead_id,
        old_status.value,
        new_status.value,
        actor_id,
    )

    # Publish Kafka event
    kafka = _get_kafka(request)
    if kafka:
        await kafka.publish_lead_status_changed(
            lead_id=lead.id,
            tenant_id=tenant_id,
            old_status=old_status.value,
            new_status=new_status.value,
            changed_by=actor_id,
            reason=payload.reason,
        )

    return LeadResponse.model_validate(lead)


# ── GET /{lead_id}/history – Status history ───────────────────────────────────

@router.get(
    "/{lead_id}/history",
    response_model=list[LeadStatusHistoryResponse],
    summary="Get status-change history for a lead",
)
async def get_lead_history(
    lead_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[LeadStatusHistoryResponse]:
    """
    Return the full ordered audit trail of pipeline status transitions for
    a given lead, oldest first.
    """
    tenant_id = _require_tenant(request)
    # Verify lead belongs to tenant
    await _get_lead_or_404(lead_id, tenant_id, db)

    result = await db.execute(
        select(LeadStatusHistory)
        .where(LeadStatusHistory.lead_id == lead_id)
        .order_by(LeadStatusHistory.created_at.asc())
    )
    history = result.scalars().all()
    return [LeadStatusHistoryResponse.model_validate(h) for h in history]
