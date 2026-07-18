import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/summary")
async def get_analytics_summary(
    tenant_id: str,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    request: Request = Depends()
):
    """
    Get general analytics summary for the dashboard:
    - Calls Handled
    - Conversion Rate
    - Meeting Booking Rate
    - Cost Saved (formula based on manual call hours vs AI hours)
    - Average Response Time (seconds from lead creation to call start)
    """
    mongo_db = request.app.state.mongo_db
    
    start_date = datetime.utcnow() - timedelta(days=days)
    
    # 1. Total Leads Ingested
    leads_query = await db.execute(
        text("SELECT COUNT(*) FROM leads WHERE tenant_id = :tenant_id AND created_at >= :start_date"),
        {"tenant_id": tenant_id, "start_date": start_date}
    )
    total_leads = leads_query.scalar() or 0
    
    # 2. Qualified Leads
    qualified_query = await db.execute(
        text("SELECT COUNT(*) FROM leads WHERE tenant_id = :tenant_id AND status IN ('qualified', 'meeting_booked') AND created_at >= :start_date"),
        {"tenant_id": tenant_id, "start_date": start_date}
    )
    qualified_leads = qualified_query.scalar() or 0
    
    # 3. Meetings Booked
    booked_query = await db.execute(
        text("SELECT COUNT(*) FROM leads WHERE tenant_id = :tenant_id AND status = 'meeting_booked' AND created_at >= :start_date"),
        {"tenant_id": tenant_id, "start_date": start_date}
    )
    meetings_booked = booked_query.scalar() or 0
    
    # 4. Conversion Rate & Meeting Booking Rate
    conversion_rate = (qualified_leads / total_leads * 100) if total_leads > 0 else 0.0
    booking_rate = (meetings_booked / total_leads * 100) if total_leads > 0 else 0.0
    
    # 5. Calls Handled & Duration from Mongo
    pipeline = [
        {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": start_date}}},
        {"$group": {
            "_id": None,
            "total_calls": {"$sum": 1},
            "total_duration": {"$sum": "$duration_seconds"},
            "avg_duration": {"$avg": "$duration_seconds"}
        }}
    ]
    
    calls_handled = 0
    total_duration_sec = 0.0
    avg_duration_sec = 0.0
    
    cursor = mongo_db.calls.aggregate(pipeline)
    async for doc in cursor:
        calls_handled = doc.get("total_calls", 0)
        total_duration_sec = doc.get("total_duration", 0.0)
        avg_duration_sec = doc.get("avg_duration", 0.0)
        
    # 6. Cost Saved Formula:
    # Human Agent Cost: ~$18/hr ($0.30/min)
    # AI Voice Agent Cost: ~0.08/min (Retell API $0.05 + Twilio $0.01 + OpenAI $0.02)
    # Savings per minute: ~$0.22/min
    duration_minutes = total_duration_sec / 60.0
    savings_per_min = 0.22
    # Plus overhead savings (leads that would require manual dialing and follow ups)
    # Let's say we save $1.50 per call handle overhead (CRM updates, notes writing etc.)
    cost_saved = (duration_minutes * savings_per_min) + (calls_handled * 1.50)
    
    # 7. Average Response Time: lead.created_at to lead.contacted_at
    response_time_query = await db.execute(
        text("""
            SELECT AVG(EXTRACT(EPOCH FROM (contacted_at - created_at))) 
            FROM leads 
            WHERE tenant_id = :tenant_id 
              AND contacted_at IS NOT NULL 
              AND created_at >= :start_date
        """),
        {"tenant_id": tenant_id, "start_date": start_date}
    )
    avg_response_time_seconds = response_time_query.scalar() or 0.0
    
    return {
        "calls_handled": calls_handled,
        "total_leads": total_leads,
        "conversion_rate": round(conversion_rate, 2),
        "meeting_booking_rate": round(booking_rate, 2),
        "cost_saved": round(cost_saved, 2),
        "avg_response_time_seconds": round(avg_response_time_seconds, 2),
        "avg_call_duration_seconds": round(avg_duration_sec, 2),
    }

@router.get("/lead-funnel")
async def get_lead_funnel(
    tenant_id: str,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db)
):
    """
    Get lead funnel stage counts for the pipeline:
    - Incoming (New)
    - Contacted
    - Qualified
    - Booked Meetings
    """
    start_date = datetime.utcnow() - timedelta(days=days)
    
    query = await db.execute(
        text("""
            SELECT status, COUNT(*) 
            FROM leads 
            WHERE tenant_id = :tenant_id AND created_at >= :start_date 
            GROUP BY status
        """),
        {"tenant_id": tenant_id, "start_date": start_date}
    )
    
    status_counts = {row[0]: row[1] for row in query.all()}
    
    # Map raw statuses to pipeline stages
    incoming = status_counts.get("new", 0)
    contacted = status_counts.get("contacted", 0) + status_counts.get("interested", 0) + status_counts.get("follow_up_required", 0)
    qualified = status_counts.get("qualified", 0)
    booked = status_counts.get("meeting_booked", 0)
    
    return {
        "stages": [
            {"stage": "Incoming Leads", "count": incoming + contacted + qualified + booked},
            {"stage": "Contacted Leads", "count": contacted + qualified + booked},
            {"stage": "Qualified Leads", "count": qualified + booked},
            {"stage": "Booked Meetings", "count": booked}
        ]
    }

@router.get("/response-times")
async def get_response_time_distribution(
    tenant_id: str,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db)
):
    """
    Get response time distribution for verification (e.g. % calls inside 1 minute).
    """
    start_date = datetime.utcnow() - timedelta(days=days)
    
    query = await db.execute(
        text("""
            SELECT 
                COUNT(*) FILTER (WHERE EXTRACT(EPOCH FROM (contacted_at - created_at)) <= 60) as under_1m,
                COUNT(*) FILTER (WHERE EXTRACT(EPOCH FROM (contacted_at - created_at)) > 60 AND EXTRACT(EPOCH FROM (contacted_at - created_at)) <= 300) as under_5m,
                COUNT(*) FILTER (WHERE EXTRACT(EPOCH FROM (contacted_at - created_at)) > 300 AND EXTRACT(EPOCH FROM (contacted_at - created_at)) <= 1800) as under_30m,
                COUNT(*) FILTER (WHERE EXTRACT(EPOCH FROM (contacted_at - created_at)) > 1800) as over_30m
            FROM leads 
            WHERE tenant_id = :tenant_id 
              AND contacted_at IS NOT NULL 
              AND created_at >= :start_date
        """),
        {"tenant_id": tenant_id, "start_date": start_date}
    )
    
    row = query.first()
    under_1m = row[0] or 0
    under_5m = row[1] or 0
    under_30m = row[2] or 0
    over_30m = row[3] or 0
    
    total = under_1m + under_5m + under_30m + over_30m
    
    return {
        "under_1_minute_pct": round((under_1m / total * 100), 2) if total > 0 else 0.0,
        "under_5_minutes_pct": round((under_5m / total * 100), 2) if total > 0 else 0.0,
        "under_30_minutes_pct": round((under_30m / total * 100), 2) if total > 0 else 0.0,
        "over_30_minutes_pct": round((over_30m / total * 100), 2) if total > 0 else 0.0,
        "raw_counts": {
            "under_1m": under_1m,
            "under_5m": under_5m,
            "under_30m": under_30m,
            "over_30m": over_30m
        }
    }
