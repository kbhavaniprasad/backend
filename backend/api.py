"""
api.py — REST API route handlers
Handles voice calls, lead submission, instant/form trigger, live chat with supervisor evaluation, and admin dashboard metrics.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

import database as db
import supervisor
import voice
from config import config
from models import (
    ChatMessageRequest,
    LeadCreate,
    StartCallRequest,
    StopCallRequest,
    TriggerAgentRequest,
)
from utils import check_rate_limit, err, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return ok("Server is running", {"version": "1.0.0"})


# ── Voice session ─────────────────────────────────────────────────────────────

@router.post("/voice/start")
async def start_voice_call(
    body: StartCallRequest,
    request: Request,
    _: None = Depends(check_rate_limit),
):
    """
    Ask Retell AI for a web call access token.
    The frontend SDK uses this token to open a WebRTC voice session.
    """
    try:
        call_data = await voice.create_web_call(agent_id=body.agent_id)

        # Persist to SQLite
        await db.save_session(
            call_id=call_data["call_id"],
            agent_id=call_data.get("agent_id", config.RETELL_AGENT_ID),
        )

        return ok("Voice call started", {
            "access_token": call_data["access_token"],
            "call_id":      call_data["call_id"],
            "agent_id":     call_data.get("agent_id", config.RETELL_AGENT_ID),
        })

    except ValueError as exc:
        logger.error("Config error: %s", exc)
        raise HTTPException(status_code=500, detail=err("Configuration error", str(exc)))

    except Exception as exc:
        logger.error("Failed to start call: %s", exc)
        raise HTTPException(status_code=502, detail=err("Could not start call. Check credentials."))


@router.post("/voice/stop")
async def stop_voice_call(body: StopCallRequest):
    """Mark a call as ended. Agent 2 analyzes the full transcript & registers classified lead."""
    try:
        await db.end_session(
            call_id=body.call_id,
            duration=body.duration_seconds,
            transcript=body.transcript,
        )

        # Agent 2 Post-Call Analysis & Lead Categorization
        if body.transcript:
            analysis = await supervisor.analyze_transcript_and_classify_lead(
                transcript=body.transcript,
                agent_type="voice",
            )
            await db.save_lead(
                name="Voice Call Lead",
                requirement=analysis["requirement"],
                summary=analysis["summary"],
                next_action=analysis["next_action"],
                source="voice_transcript",
                agent_type="voice",
                lead_score=analysis["lead_score"],
                status=analysis["status"],
            )

        return ok("Call ended. Agent 2 analyzed transcript & registered lead.")
    except Exception as exc:
        logger.error("Failed to end session: %s", exc)
        raise HTTPException(status_code=500, detail=err("Failed to save session"))


# ── Session history ───────────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions():
    """Return the last 20 call sessions from SQLite."""
    try:
        sessions = await db.get_sessions(limit=20)
        return ok("Sessions fetched", sessions)
    except Exception as exc:
        logger.error("Failed to fetch sessions: %s", exc)
        raise HTTPException(status_code=500, detail=err("Failed to fetch sessions"))


@router.delete("/sessions/{session_id}")
async def remove_session(session_id: int):
    """Delete a single session by its database ID."""
    deleted = await db.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=err("Session not found"))
    return ok("Session deleted")


# ── Agent info ────────────────────────────────────────────────────────────────

@router.get("/agent")
async def agent_info():
    """Return the configured Retell agent's metadata."""
    try:
        info = await voice.get_agent_info()
        return ok("Agent info fetched", info)
    except Exception as exc:
        logger.error("Failed to fetch agent: %s", exc)
        raise HTTPException(status_code=502, detail=err("Could not fetch agent info"))


# ── Lead Capture & Engagement Flow ───────────────────────────────────────────

@router.post("/leads")
async def create_lead(body: LeadCreate):
    """
    Flow 2: Registration Form submit.
    Stores the lead in DB with automatic qualification score.
    """
    try:
        # Score calculation heuristic based on requirement & company presence
        score = 70
        if body.company:
            score += 15
        if body.phone:
            score += 10
        if body.requirement and len(body.requirement) > 10:
            score += 5

        lead = await db.save_lead(
            name=body.name,
            email=body.email,
            phone=body.phone,
            company=body.company,
            requirement=body.requirement,
            source=body.source,
            agent_type=body.agent_type,
            lead_score=min(100, score),
        )
        return ok("Lead registered successfully", lead)
    except Exception as exc:
        logger.error("Failed to save lead: %s", exc)
        raise HTTPException(status_code=500, detail=err("Could not store lead"))


@router.get("/leads")
async def get_leads():
    """List all leads for admin dashboard."""
    try:
        leads = await db.get_leads()
        return ok("Leads fetched", leads)
    except Exception as exc:
        logger.error("Failed to list leads: %s", exc)
        raise HTTPException(status_code=500, detail=err("Could not fetch leads"))


@router.post("/trigger-agent")
async def trigger_agent(body: TriggerAgentRequest):
    """
    Automatically activate Voice or Chat Agent for a user or lead.
    Called immediately after form submission or instant CTA click.
    """
    session_id = f"session_{uuid.uuid4().hex[:12]}"
    
    if body.agent_type == "voice":
        # Returns call credentials for frontend WebRTC
        call_data = await voice.create_web_call()
        await db.save_session(
            call_id=call_data["call_id"],
            agent_id=call_data.get("agent_id", config.RETELL_AGENT_ID),
        )
        return ok("Voice agent activated", {
            "session_id": session_id,
            "agent_type": "voice",
            "access_token": call_data["access_token"],
            "call_id": call_data["call_id"],
        })
    else:
        # Chat agent initial message
        initial_msg = "Hi! Thanks for showing interest. I'm your AI sales assistant. What can I help you build today?"
        await db.save_chat_message(
            session_id=session_id,
            role="agent",
            content=initial_msg,
        )
        return ok("Chat agent activated", {
            "session_id": session_id,
            "agent_type": "chat",
            "initial_message": initial_msg,
        })


# ── Live Chat + Supervisor AI Evaluation ──────────────────────────────────────

@router.post("/chat/message")
async def send_chat_message(body: ChatMessageRequest):
    """
    Flow: User sends message → Agent 1 (Sales AI) generates initial answer
    → Agent 2 (Supervisor AI) evaluates in real-time → returns final response.
    """
    try:
        # 1. Store User Message
        await db.save_chat_message(
            session_id=body.session_id,
            role="user",
            content=body.message,
        )

        # 2. Agent 1 (Sales AI) Draft Response
        raw_agent_reply = _generate_sales_agent_reply(body.message)

        # 3. Agent 2 (Supervisor AI) Real-Time Evaluation
        eval_result = await supervisor.evaluate_response(
            user_message=body.message,
            agent_response=raw_agent_reply,
        )

        final_content = eval_result["corrected_response"]
        was_corrected = not eval_result["is_correct"]

        # 4. Store Agent Message in Database with evaluation metrics
        saved_msg = await db.save_chat_message(
            session_id=body.session_id,
            role="agent",
            content=final_content,
            original_content=raw_agent_reply if was_corrected else None,
            corrected=was_corrected,
            correction_reason=eval_result.get("reason"),
            quality_score=eval_result.get("quality_score", 95),
        )

        return ok("Message processed", {
            "message": saved_msg,
            "supervisor_evaluation": {
                "is_correct": eval_result["is_correct"],
                "original_response": raw_agent_reply,
                "corrected_response": final_content,
                "reason": eval_result.get("reason"),
                "quality_score": eval_result.get("quality_score", 95),
            }
        })

    except Exception as exc:
        logger.error("Failed to process chat message: %s", exc)
        raise HTTPException(status_code=500, detail=err("Could not process chat message"))


@router.get("/chat/{session_id}/history")
async def chat_history(session_id: str):
    """Fetch message history for a given chat session."""
    try:
        history = await db.get_chat_history(session_id)
        return ok("Chat history fetched", history)
    except Exception as exc:
        logger.error("Failed to fetch chat history: %s", exc)
        raise HTTPException(status_code=500, detail=err("Could not fetch chat history"))


@router.post("/chat/end")
async def end_chat_session(session_id: str):
    """
    Once chat ends, Agent 2 collects the chat transcript, analyzes intent & status,
    and registers the classified lead into the lead database.
    """
    try:
        history = await db.get_chat_history(session_id)
        if not history:
            return ok("No chat history to analyze")

        transcript_lines = [f"{msg['role'].capitalize()}: {msg['content']}" for msg in history]
        full_transcript = "\n".join(transcript_lines)

        analysis = await supervisor.analyze_transcript_and_classify_lead(
            transcript=full_transcript,
            agent_type="chat",
        )

        lead = await db.save_lead(
            name="Live Chat Lead",
            requirement=analysis["requirement"],
            summary=analysis["summary"],
            next_action=analysis["next_action"],
            source="chat_transcript",
            agent_type="chat",
            lead_score=analysis["lead_score"],
            status=analysis["status"],
        )
        return ok("Chat ended. Agent 2 analyzed transcript & registered lead", lead)
    except Exception as exc:
        logger.error("Failed to end chat session: %s", exc)
        raise HTTPException(status_code=500, detail=err("Could not finalize chat session"))


# ── Admin Dashboard Statistics ────────────────────────────────────────────────

@router.get("/dashboard/stats")
async def dashboard_stats():
    """Return live metrics, conversion, quality scores, and supervisor corrections."""
    try:
        stats = await db.get_dashboard_stats()
        return ok("Dashboard stats fetched", stats)
    except Exception as exc:
        logger.error("Failed to fetch dashboard stats: %s", exc)
        raise HTTPException(status_code=500, detail=err("Could not fetch dashboard stats"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generate_sales_agent_reply(user_msg: str) -> str:
    """
    Sales AI Agent (Agent 1) initial draft response generator.
    Simulates real agent answers (including intentional edge case gaps
    for Supervisor AI to catch and demonstrate real-time quality correction).
    """
    msg = user_msg.lower()
    
    if "whatsapp" in msg:
        # Intentionally flawed answer so Supervisor AI corrects it!
        return "No, we do not currently support WhatsApp integration."
    elif "cancel" in msg or "contract" in msg:
        # Intentionally vague answer for Supervisor AI to fix!
        return "I don't know the exact cancellation terms off the top of my head."
    elif "price" in msg or "cost" in msg:
        return "Our pricing depends on your team size. Starter plan starts at $49/month."
    elif "demo" in msg or "meeting" in msg:
        return "I'd love to set up a quick 15-minute product walkthrough. What time works best for you?"
    elif "human" in msg or "agent" in msg:
        return "I am an AI assistant available 24/7 to answer your questions!"
    else:
        return "That sounds great! Our platform automates lead qualification and instant response. Would you like to schedule a quick demo?"
