"""
app/routers/webhooks.py – Inbound webhook handlers for all lead acquisition channels.

Prefix  : /api/v1/webhooks
Auth    : Per-webhook signature / token verification (no JWT required)
Events  : Each successful webhook creates a Lead and publishes a Kafka event

Supported channels
------------------
- Facebook Lead Ads  (GET for challenge, POST for lead data)
- Google Ads Lead Form
- LinkedIn Lead Gen Forms
- Generic CRM (Salesforce / HubSpot / Zoho)
- Website contact form
- WhatsApp Cloud API incoming messages
"""

import hashlib
import hmac
import json
import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.lead import Lead, LeadSource, LeadStatus
from app.schemas.lead import (
    WebhookCRMPayload,
    WebhookFacebookLead,
    WebhookGoogleLead,
    WebhookLinkedInLead,
    WebhookWebsiteForm,
    WebhookWhatsAppMessage,
)

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(
    prefix="/api/v1/webhooks",
    tags=["Webhooks"],
)

# ── Supported CRM types ───────────────────────────────────────────────────────
SUPPORTED_CRM_TYPES = {"salesforce", "hubspot", "zoho"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_kafka(request: Request):
    return request.app.state.kafka_producer


def _get_default_tenant(request: Request) -> uuid.UUID:
    """
    Resolve a tenant_id for webhook events.

    Webhooks arrive without a JWT, so the tenant is identified by:
    1. X-Tenant-ID header set by the API gateway / proxy.
    2. A static fallback UUID representing the default / single-tenant setup.
    """
    raw = request.headers.get("X-Tenant-ID")
    if raw:
        try:
            return uuid.UUID(raw)
        except ValueError:
            pass
    # Fallback – in production the API gateway should always set this header
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _save_lead_and_publish(
    *,
    db: AsyncSession,
    kafka,
    tenant_id: uuid.UUID,
    source: LeadSource,
    first_name: str,
    last_name: str,
    email: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    job_title: str | None = None,
    city: str | None = None,
    country: str | None = None,
    external_id: str | None = None,
    ad_campaign_id: str | None = None,
    ad_set_id: str | None = None,
    ad_id: str | None = None,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
    raw_data: dict[str, Any] | None = None,
) -> Lead:
    """
    Persist a new Lead record and publish the ``lead.created`` Kafka event.

    Shared by all webhook handlers to ensure consistent behaviour.
    """
    lead = Lead(
        tenant_id=tenant_id,
        source=source,
        status=LeadStatus.new,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        company=company,
        job_title=job_title,
        city=city,
        country=country,
        external_id=external_id,
        ad_campaign_id=ad_campaign_id,
        ad_set_id=ad_set_id,
        ad_id=ad_id,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        raw_data=raw_data,
    )
    db.add(lead)
    await db.flush()
    await db.refresh(lead)

    if kafka:
        await kafka.publish_lead_created(
            lead_id=lead.id,
            tenant_id=tenant_id,
            lead_data={
                "source": source.value,
                "phone": phone,
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
            },
        )

    return lead


def _verify_fb_signature(body: bytes, signature_header: str | None) -> None:
    """
    Validate the X-Hub-Signature-256 header sent by Facebook.

    Raises HTTP 403 if the signature is absent or invalid.
    """
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-Hub-Signature-256 header.",
        )
    try:
        algo, digest = signature_header.split("=", 1)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Malformed X-Hub-Signature-256 header.",
        )

    expected = hmac.new(
        settings.facebook_app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, digest):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Facebook webhook signature verification failed.",
        )


async def _fetch_facebook_lead_data(leadgen_id: str) -> dict[str, Any]:
    """
    Retrieve lead field values from the Facebook Graph API.

    Returns an empty dict on failure so processing can continue.
    """
    url = (
        f"https://graph.facebook.com/{settings.facebook_api_version}"
        f"/{leadgen_id}?access_token={settings.facebook_access_token}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch Facebook lead data for %s: %s", leadgen_id, exc)
        return {}


# ── Facebook ──────────────────────────────────────────────────────────────────

@router.get(
    "/facebook",
    summary="Facebook webhook verification (hub.challenge)",
    response_model=None,
)
async def facebook_verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
):
    """
    Handle Facebook's webhook subscription verification request.

    Facebook sends a GET with ``hub.mode=subscribe`` and a challenge string.
    We echo the challenge back if our verify token matches.
    """
    if hub_mode != "subscribe":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid hub.mode.",
        )
    if hub_verify_token != settings.facebook_verify_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify token mismatch.",
        )
    # Return the challenge as plain text (Facebook requires this)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(hub_challenge)


@router.post(
    "/facebook",
    status_code=status.HTTP_200_OK,
    summary="Process Facebook Lead Ads webhook",
)
async def facebook_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None),
):
    """
    Receive and process Facebook Lead Ads change notifications.

    Flow
    ----
    1. Verify HMAC signature (X-Hub-Signature-256).
    2. Parse the entry list from the payload.
    3. For each ``leadgen`` change, fetch the full lead data from the Graph API.
    4. Create a Lead record and publish ``lead.created``.

    Facebook expects a 200 response within 20 seconds.
    """
    body = await request.body()
    _verify_fb_signature(body, x_hub_signature_256)

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        )

    tenant_id = _get_default_tenant(request)
    kafka = _get_kafka(request)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue

            value = change.get("value", {})
            leadgen_id = value.get("leadgen_id")
            if not leadgen_id:
                continue

            # Fetch full lead data from Graph API
            lead_data = await _fetch_facebook_lead_data(leadgen_id)
            field_data = {
                item["name"]: item["values"][0]
                for item in lead_data.get("field_data", [])
                if item.get("values")
            }

            full_name = field_data.get("full_name", "")
            name_parts = full_name.split(" ", 1) if full_name else ["Unknown", ""]

            try:
                await _save_lead_and_publish(
                    db=db,
                    kafka=kafka,
                    tenant_id=tenant_id,
                    source=LeadSource.facebook_ads,
                    first_name=field_data.get("first_name", name_parts[0]),
                    last_name=field_data.get("last_name", name_parts[1] if len(name_parts) > 1 else ""),
                    email=field_data.get("email"),
                    phone=field_data.get("phone_number"),
                    company=field_data.get("company_name"),
                    job_title=field_data.get("job_title"),
                    city=field_data.get("city"),
                    country=field_data.get("country"),
                    external_id=leadgen_id,
                    ad_campaign_id=value.get("ad_campaign_id"),
                    ad_set_id=value.get("ad_group_id"),
                    ad_id=value.get("ad_id"),
                    raw_data=lead_data,
                )
                logger.info(
                    "Facebook lead created. leadgen_id=%s tenant_id=%s",
                    leadgen_id,
                    tenant_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Error processing Facebook lead %s: %s", leadgen_id, exc)

    return {"status": "ok"}


# ── Google Ads ────────────────────────────────────────────────────────────────

@router.post(
    "/google",
    status_code=status.HTTP_200_OK,
    summary="Process Google Ads lead form webhook",
)
async def google_webhook(
    payload: WebhookGoogleLead,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_google_signature: str | None = Header(default=None),
):
    """
    Receive a Google Ads Lead Form extension webhook.

    Extracts user column data (name, email, phone) and creates a Lead.
    """
    # Optional HMAC signature check
    if settings.google_webhook_secret and x_google_signature:
        body = await request.body()
        expected = hmac.new(
            settings.google_webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, x_google_signature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Google webhook signature mismatch.",
            )

    tenant_id = _get_default_tenant(request)
    kafka = _get_kafka(request)

    # Extract user fields
    columns = {col["column_id"]: col.get("string_value", "") for col in payload.user_column_data}

    full_name = columns.get("FULL_NAME", "")
    name_parts = full_name.split(" ", 1) if full_name else ["Unknown", ""]

    try:
        await _save_lead_and_publish(
            db=db,
            kafka=kafka,
            tenant_id=tenant_id,
            source=LeadSource.google_ads,
            first_name=columns.get("GIVEN_NAME", name_parts[0]),
            last_name=columns.get("FAMILY_NAME", name_parts[1] if len(name_parts) > 1 else ""),
            email=columns.get("EMAIL"),
            phone=columns.get("PHONE_NUMBER"),
            company=columns.get("COMPANY_NAME"),
            job_title=columns.get("JOB_TITLE"),
            city=columns.get("CITY"),
            country=columns.get("COUNTRY"),
            external_id=payload.lead_id,
            ad_campaign_id=payload.campaign_id,
            ad_set_id=payload.ad_group_id,
            ad_id=payload.creative_id,
            raw_data=payload.model_dump(),
        )
        logger.info(
            "Google lead created. lead_id=%s tenant_id=%s",
            payload.lead_id,
            tenant_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Error processing Google lead: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process Google lead.",
        )

    return {"status": "ok"}


# ── LinkedIn ──────────────────────────────────────────────────────────────────

@router.post(
    "/linkedin",
    status_code=status.HTTP_200_OK,
    summary="Process LinkedIn Lead Gen form webhook",
)
async def linkedin_webhook(
    payload: WebhookLinkedInLead,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_linkedin_signature: str | None = Header(default=None),
):
    """
    Receive a LinkedIn Lead Gen Forms webhook submission.

    LinkedIn sends form responses as key-value pairs inside ``form_response``.
    """
    # Signature verification
    if settings.linkedin_webhook_secret and x_linkedin_signature:
        body = await request.body()
        expected = hmac.new(
            settings.linkedin_webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, x_linkedin_signature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="LinkedIn webhook signature mismatch.",
            )

    tenant_id = _get_default_tenant(request)
    kafka = _get_kafka(request)

    fr = payload.form_response

    first_name = fr.get("firstName") or fr.get("first_name") or "Unknown"
    last_name = fr.get("lastName") or fr.get("last_name") or ""
    email = fr.get("emailAddress") or fr.get("email")
    phone = fr.get("phoneNumber") or fr.get("phone")

    try:
        await _save_lead_and_publish(
            db=db,
            kafka=kafka,
            tenant_id=tenant_id,
            source=LeadSource.linkedin_ads,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            company=fr.get("company"),
            job_title=fr.get("title") or fr.get("jobTitle"),
            external_id=payload.submission_id,
            ad_campaign_id=payload.campaign_id,
            ad_id=payload.creative_id,
            raw_data=payload.model_dump(),
        )
        logger.info(
            "LinkedIn lead created. submission_id=%s tenant_id=%s",
            payload.submission_id,
            tenant_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Error processing LinkedIn lead: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process LinkedIn lead.",
        )

    return {"status": "ok"}


# ── Generic CRM ───────────────────────────────────────────────────────────────

@router.post(
    "/crm/{crm_type}",
    status_code=status.HTTP_200_OK,
    summary="Generic CRM webhook (salesforce / hubspot / zoho)",
)
async def crm_webhook(
    crm_type: str,
    payload: WebhookCRMPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Accept lead / contact create/update events from supported CRMs.

    ``crm_type`` must be one of: ``salesforce``, ``hubspot``, ``zoho``.

    Field mapping is CRM-specific and handled below.
    """
    if crm_type not in SUPPORTED_CRM_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported CRM type '{crm_type}'. Supported: {sorted(SUPPORTED_CRM_TYPES)}.",
        )

    tenant_id = _get_default_tenant(request)
    kafka = _get_kafka(request)
    data = payload.data

    # ── Field normalisation per CRM ─────────────────────────────────
    if crm_type == "hubspot":
        props = data.get("properties", data)
        first_name = props.get("firstname", "")
        last_name = props.get("lastname", "")
        email = props.get("email")
        phone = props.get("phone")
        company = props.get("company")
        job_title = props.get("jobtitle")
        external_id = str(data.get("id") or payload.record_id or "")
    elif crm_type == "salesforce":
        first_name = data.get("FirstName", "")
        last_name = data.get("LastName", "")
        email = data.get("Email")
        phone = data.get("Phone") or data.get("MobilePhone")
        company = data.get("Company")
        job_title = data.get("Title")
        external_id = str(data.get("Id") or payload.record_id or "")
    else:  # zoho
        first_name = data.get("First_Name", "")
        last_name = data.get("Last_Name", "")
        email = data.get("Email")
        phone = data.get("Phone") or data.get("Mobile")
        company = data.get("Company") or data.get("Account_Name")
        job_title = data.get("Title")
        external_id = str(data.get("id") or payload.record_id or "")

    if not first_name:
        first_name = "Unknown"

    try:
        await _save_lead_and_publish(
            db=db,
            kafka=kafka,
            tenant_id=tenant_id,
            source=LeadSource.crm,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            company=company,
            job_title=job_title,
            external_id=external_id or None,
            raw_data={**payload.model_dump(), "crm_type": crm_type},
        )
        logger.info(
            "CRM lead created. crm=%s record_id=%s tenant_id=%s",
            crm_type,
            external_id,
            tenant_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Error processing CRM lead (%s): %s", crm_type, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process {crm_type} lead.",
        )

    return {"status": "ok"}


# ── Website form ──────────────────────────────────────────────────────────────

@router.post(
    "/website-form",
    status_code=status.HTTP_201_CREATED,
    summary="Website contact form submission",
)
async def website_form_webhook(
    payload: WebhookWebsiteForm,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Accept a contact form submission from the tenant's website.

    Implements a honeypot field check: if the ``website`` field (hidden in
    the real form) is non-empty, the request is silently discarded as a bot.
    """
    # Honeypot bot detection
    if payload.website:
        logger.info("Honeypot triggered – discarding bot submission.")
        return {"status": "ok"}  # silent discard

    if not payload.email and not payload.phone:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of 'email' or 'phone' must be provided.",
        )

    tenant_id = _get_default_tenant(request)
    kafka = _get_kafka(request)

    try:
        lead = await _save_lead_and_publish(
            db=db,
            kafka=kafka,
            tenant_id=tenant_id,
            source=LeadSource.website_form,
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=str(payload.email) if payload.email else None,
            phone=payload.phone,
            company=payload.company,
            utm_source=payload.utm_source,
            utm_medium=payload.utm_medium,
            utm_campaign=payload.utm_campaign,
            raw_data={
                "message": payload.message,
                **payload.model_dump(exclude={"website"}),
            },
        )
        logger.info(
            "Website form lead created. lead_id=%s tenant_id=%s",
            lead.id,
            tenant_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Error processing website form submission: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process form submission.",
        )

    return {"status": "created", "lead_id": str(lead.id)}


# ── WhatsApp ──────────────────────────────────────────────────────────────────

@router.get(
    "/whatsapp",
    summary="WhatsApp webhook verification",
    response_model=None,
)
async def whatsapp_verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
):
    """Verify the WhatsApp Cloud API webhook subscription."""
    if hub_mode != "subscribe" or hub_verify_token != settings.whatsapp_verify_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="WhatsApp verification failed.",
        )
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(hub_challenge)


@router.post(
    "/whatsapp",
    status_code=status.HTTP_200_OK,
    summary="WhatsApp incoming message webhook",
)
async def whatsapp_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None),
):
    """
    Receive inbound WhatsApp messages via the Meta Cloud API.

    Each unique sender (wa_id / phone number) that messages the business
    is captured as a new lead if they have not already been seen.

    Flow
    ----
    1. Verify HMAC signature.
    2. Extract sender phone number and name from the contact block.
    3. Upsert a Lead with source=whatsapp.
    """
    body = await request.body()

    # Signature verification (reuses Facebook App Secret for Meta services)
    if settings.whatsapp_app_secret:
        if not x_hub_signature_256:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing X-Hub-Signature-256 header.",
            )
        expected = hmac.new(
            settings.whatsapp_app_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        _, digest = x_hub_signature_256.split("=", 1)
        if not hmac.compare_digest(expected, digest):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="WhatsApp webhook signature verification failed.",
            )

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        )

    tenant_id = _get_default_tenant(request)
    kafka = _get_kafka(request)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = value.get("contacts", [])
            messages = value.get("messages", [])

            if not messages:
                continue

            for contact in contacts:
                wa_id = contact.get("wa_id")  # phone number in E.164 format
                profile = contact.get("profile", {})
                display_name = profile.get("name", "")

                name_parts = display_name.split(" ", 1) if display_name else ["Unknown", ""]
                first_name = name_parts[0]
                last_name = name_parts[1] if len(name_parts) > 1 else ""
                phone = f"+{wa_id}" if wa_id and not wa_id.startswith("+") else wa_id

                try:
                    await _save_lead_and_publish(
                        db=db,
                        kafka=kafka,
                        tenant_id=tenant_id,
                        source=LeadSource.whatsapp,
                        first_name=first_name,
                        last_name=last_name,
                        phone=phone,
                        external_id=wa_id,
                        raw_data={"entry": entry, "contact": contact},
                    )
                    logger.info(
                        "WhatsApp lead created. wa_id=%s tenant_id=%s",
                        wa_id,
                        tenant_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Error processing WhatsApp message from %s: %s", wa_id, exc
                    )

    return {"status": "ok"}
