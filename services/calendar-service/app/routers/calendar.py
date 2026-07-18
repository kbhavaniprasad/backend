import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class BookMeetingRequest(BaseModel):
    tenant_id: str
    lead_id: str
    title: str
    description: Optional[str] = None
    starts_at: datetime
    duration_minutes: int = 30
    timezone: str = "UTC"
    ai_booked: bool = True


class CalendarConnectRequest(BaseModel):
    tenant_id: str
    user_id: str
    calendar_type: str  # google, outlook
    access_token: str
    refresh_token: Optional[str] = None
    expires_in_seconds: Optional[int] = 3600
    calendar_id: Optional[str] = "primary"


@router.post("/book", status_code=status.HTTP_201_CREATED)
async def book_meeting(
    body: BookMeetingRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Book a meeting on behalf of a tenant.
    1. Fetches OAuth credentials for calendar integration.
    2. Writes to postgres 'meetings' table.
    3. Simulates/Triggers Google/Outlook Calendar event creation.
    4. Publishes Kafka event so lead status is updated to 'meeting_booked'.
    """
    # Verify lead exists
    lead_query = await db.execute(
        text("SELECT id, email, first_name, last_name FROM leads WHERE id = :lead_id AND tenant_id = :tenant_id"),
        {"lead_id": body.lead_id, "tenant_id": body.tenant_id}
    )
    lead = lead_query.fetchone()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
        
    lead_email = lead[1]
    lead_name = f"{lead[2]} {lead[3]}".strip()
    
    # Calculate end time
    ends_at = body.starts_at + timedelta(minutes=body.duration_minutes)
    
    # Fetch calendar integration details
    integration_query = await db.execute(
        text("""
            SELECT calendar_type, access_token, refresh_token, calendar_id 
            FROM calendar_integrations 
            WHERE tenant_id = :tenant_id AND is_active = true 
            LIMIT 1
        """),
        {"tenant_id": body.tenant_id}
    )
    integration = integration_query.fetchone()
    
    calendar_event_id = f"mock_evt_{uuid.uuid4().hex[:12]}"
    meeting_link = f"https://meet.google.com/mock-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:4]}"
    calendar_type = "google"
    
    if integration:
        calendar_type = integration[0]
        # In a real system, we'd make a request to Google/Microsoft API using access_token:
        # headers = {"Authorization": f"Bearer {integration[1]}"}
        # payload = {"summary": body.title, "start": {"dateTime": body.starts_at.isoformat()}, ...}
        # response = httpx.post("https://www.googleapis.com/calendar/v3/calendars/primary/events", json=payload, headers=headers)
        # and parse actual calendar_event_id + hangoutLink/meeting_link
        logger.info(
            "Integration found for tenant %s. Simulating %s event creation.", 
            body.tenant_id, calendar_type
        )
    else:
        logger.info(
            "No calendar integration found for tenant %s. Booking as manual calendar entry.", 
            body.tenant_id
        )
        calendar_type = "manual"
        meeting_link = "https://meet.google.com/manual-call-link"

    # Insert meeting into Postgres
    meeting_id = uuid.uuid4()
    await db.execute(
        text("""
            INSERT INTO meetings (
                id, tenant_id, lead_id, title, description, meeting_link, 
                status, calendar_type, calendar_event_id, starts_at, ends_at, 
                duration_minutes, timezone, ai_booked, created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :lead_id, :title, :description, :meeting_link, 
                'scheduled', :calendar_type, :calendar_event_id, :starts_at, :ends_at, 
                :duration_minutes, :timezone, :ai_booked, NOW(), NOW()
            )
        """),
        {
            "id": meeting_id,
            "tenant_id": body.tenant_id,
            "lead_id": body.lead_id,
            "title": body.title,
            "description": body.description,
            "meeting_link": meeting_link,
            "calendar_type": calendar_type,
            "calendar_event_id": calendar_event_id,
            "starts_at": body.starts_at,
            "ends_at": ends_at,
            "duration_minutes": body.duration_minutes,
            "timezone": body.timezone,
            "ai_booked": body.ai_booked,
        }
    )
    
    # Update lead status to 'meeting_booked'
    await db.execute(
        text("UPDATE leads SET status = 'meeting_booked', updated_at = NOW() WHERE id = :lead_id"),
        {"lead_id": body.lead_id}
    )
    
    # Write to status history
    await db.execute(
        text("""
            INSERT INTO lead_status_history (id, lead_id, old_status, new_status, reason, created_at)
            VALUES (uuid_generate_v4(), :lead_id, 'qualified', 'meeting_booked', 'Meeting scheduled by Agent A', NOW())
        """),
        {"lead_id": body.lead_id}
    )
    
    await db.commit()
    
    # 5. Publish Kafka Event
    kafka_producer = request.app.state.kafka_producer
    await kafka_producer.send_and_wait(
        "meeting.booked",
        value={
            "event": "meeting.booked",
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": body.tenant_id,
            "lead_id": body.lead_id,
            "meeting_id": str(meeting_id),
            "starts_at": body.starts_at.isoformat(),
            "duration_minutes": body.duration_minutes,
            "calendar_type": calendar_type,
            "ai_booked": body.ai_booked
        },
        key=body.tenant_id
    )
    
    logger.info("Meeting booked: %s", meeting_id)
    
    return {
        "meeting_id": str(meeting_id),
        "calendar_event_id": calendar_event_id,
        "meeting_link": meeting_link,
        "starts_at": body.starts_at,
        "ends_at": ends_at,
        "status": "scheduled",
    }


@router.post("/connect")
async def connect_calendar(
    body: CalendarConnectRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Connect a user's Google or Microsoft Outlook calendar.
    Saves OAuth tokens for integration.
    """
    token_expires = datetime.utcnow() + timedelta(seconds=body.expires_in_seconds or 3600)
    
    # Upsert calendar integration
    await db.execute(
        text("""
            INSERT INTO calendar_integrations (
                id, tenant_id, user_id, calendar_type, access_token, 
                refresh_token, token_expires, calendar_id, is_active, created_at, updated_at
            ) VALUES (
                uuid_generate_v4(), :tenant_id, :user_id, :calendar_type, :access_token, 
                :refresh_token, :token_expires, :calendar_id, true, NOW(), NOW()
            )
            ON CONFLICT (tenant_id, user_id, calendar_type) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = COALESCE(EXCLUDED.refresh_token, calendar_integrations.refresh_token),
                token_expires = EXCLUDED.token_expires,
                is_active = true,
                updated_at = NOW()
        """),
        {
            "tenant_id": body.tenant_id,
            "user_id": body.user_id,
            "calendar_type": body.calendar_type,
            "access_token": body.access_token,
            "refresh_token": body.refresh_token,
            "token_expires": token_expires,
            "calendar_id": body.calendar_id,
        }
    )
    await db.commit()
    return {"status": "connected", "calendar_type": body.calendar_type}


@router.get("/slots")
async def get_available_slots(
    tenant_id: str,
    date: str = Query(..., description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get available meeting slots for a given date.
    Calculates slots by checking existing meetings and business hours.
    """
    try:
        query_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
    start_time = datetime.combine(query_date, datetime.min.time())
    end_time = datetime.combine(query_date, datetime.max.time())
    
    # Fetch scheduled meetings for the tenant on this day
    meetings_query = await db.execute(
        text("""
            SELECT starts_at, ends_at 
            FROM meetings 
            WHERE tenant_id = :tenant_id 
              AND status = 'scheduled'
              AND starts_at >= :start_time 
              AND ends_at <= :end_time
        """),
        {"tenant_id": tenant_id, "start_time": start_time, "end_time": end_time}
    )
    meetings = meetings_query.fetchall()
    
    # ── Calculate Free Slots ──────────────────────────────────
    # Business hours: 09:00 to 18:00
    business_start = 9
    business_end = 18
    slot_duration = 30 # minutes
    
    slots = []
    current_slot = datetime.combine(query_date, datetime.min.time()).replace(hour=business_start)
    day_end = datetime.combine(query_date, datetime.min.time()).replace(hour=business_end)
    
    while current_slot < day_end:
        slot_end = current_slot + timedelta(minutes=slot_duration)
        
        # Check if slot overlaps with any scheduled meetings
        conflict = False
        for m_start, m_end in meetings:
            # Shift back to timezone naive or handle tz-aware comparison
            # In PostgreSQL starts_at is TIMESTAMPTZ, so we make current_slot tz-aware
            # assuming UTC for simplicity here
            m_start_naive = m_start.replace(tzinfo=None)
            m_end_naive = m_end.replace(tzinfo=None)
            
            if (current_slot >= m_start_naive and current_slot < m_end_naive) or \
               (slot_end > m_start_naive and slot_end <= m_end_naive):
                conflict = True
                break
                
        if not conflict:
            slots.append(current_slot.strftime("%H:%M"))
            
        current_slot = slot_end
        
    return {"date": date, "available_slots": slots}
