"""
Calls Router — Voice Service
Manage and query Retell AI calls.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


class InitiateCallRequest(BaseModel):
    lead_id: str
    tenant_id: str
    phone_number: str
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    source: str = ""
    job_title: str = ""
    from_number: Optional[str] = None


class InitiateWebCallRequest(BaseModel):
    lead_id: str
    tenant_id: str
    first_name: str = ""
    source: str = "website"


@router.post("/initiate", status_code=status.HTTP_201_CREATED, summary="Initiate Retell AI Phone Call")
async def initiate_call(request: Request, body: InitiateCallRequest):
    """
    Trigger an outbound AI phone call to a lead via Retell AI.
    Used for manual call initiation or testing. Normally triggered automatically by Kafka.
    """
    from app.retell.orchestrator import CallOrchestrator
    orchestrator = CallOrchestrator(
        retell_client=request.app.state.retell,
        redis=request.app.state.redis,
        db=request.app.state.db,
        settings=settings,
    )

    lead_data = {
        "first_name": body.first_name,
        "last_name": body.last_name,
        "company": body.company,
        "source": body.source,
        "job_title": body.job_title,
    }

    result = await orchestrator.initiate_call(
        lead_id=body.lead_id,
        tenant_id=body.tenant_id,
        phone_number=body.phone_number,
        lead_data=lead_data,
        from_number=body.from_number,
    )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Call skipped — lead already active or max retries reached",
        )

    return {"call_id": result["call_id"], "status": result.get("call_status"), "provider": "retell-ai"}


@router.post("/web-call", status_code=status.HTTP_201_CREATED, summary="Create Web Call (WebRTC)")
async def create_web_call(request: Request, body: InitiateWebCallRequest):
    """
    Create a browser-based WebRTC call via Retell AI.
    Returns an access_token for the Retell Web SDK.
    """
    from app.retell.orchestrator import CallOrchestrator
    orchestrator = CallOrchestrator(
        retell_client=request.app.state.retell,
        redis=request.app.state.redis,
        db=request.app.state.db,
        settings=settings,
    )

    result = await orchestrator.initiate_web_call(
        lead_id=body.lead_id,
        tenant_id=body.tenant_id,
        lead_data={"first_name": body.first_name, "source": body.source},
    )
    return result


@router.get("/{call_id}", summary="Get Call Details")
async def get_call(call_id: str, request: Request):
    """Get full call details from MongoDB (includes transcript after call ends)."""
    db = request.app.state.db
    call = await db.calls.find_one({"call_id": call_id}, {"_id": 0})
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@router.get("/{call_id}/retell", summary="Get Live Call from Retell API")
async def get_retell_call(call_id: str, request: Request):
    """Fetch real-time call data directly from Retell AI API."""
    retell = request.app.state.retell
    try:
        return await retell.get_call(call_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{call_id}/end", summary="End Active Call")
async def end_call(call_id: str, request: Request):
    """Programmatically end an ongoing Retell AI call."""
    retell = request.app.state.retell
    result = await retell.end_call(call_id)
    return {"call_id": call_id, "message": "Call ended", "result": result}


@router.get("/", summary="List Calls")
async def list_calls(
    request: Request,
    tenant_id: Optional[str] = Query(None),
    lead_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    skip: int = Query(0),
):
    """List calls with optional filters."""
    db = request.app.state.db
    query: dict = {}
    if tenant_id:
        query["tenant_id"] = tenant_id
    if lead_id:
        query["lead_id"] = lead_id
    if status:
        query["status"] = status

    cursor = db.calls.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
    calls = await cursor.to_list(length=limit)
    total = await db.calls.count_documents(query)

    return {"items": calls, "total": total, "limit": limit, "skip": skip}


@router.get("/{call_id}/transcript", summary="Get Call Transcript")
async def get_transcript(call_id: str, request: Request):
    """Get the full conversation transcript for a completed call."""
    db = request.app.state.db
    call = await db.calls.find_one(
        {"call_id": call_id},
        {"transcript": 1, "transcript_object": 1, "call_summary": 1, "call_analysis": 1, "_id": 0},
    )
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call
