"""
database.py — SQLite persistence using aiosqlite
Stores call sessions, leads, chat messages, and supervisor evaluations.
No external database server needed.
"""

from __future__ import annotations

import logging
from datetime import datetime

import aiosqlite

from config import config

logger = logging.getLogger(__name__)

DB_PATH = config.DB_PATH

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS call_sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id          TEXT    NOT NULL UNIQUE,
    agent_id         TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'active',
    started_at       TEXT    NOT NULL,
    ended_at         TEXT,
    duration_seconds INTEGER,
    transcript       TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT,
    phone       TEXT,
    company     TEXT,
    requirement TEXT,
    summary     TEXT,
    next_action TEXT,
    source      TEXT DEFAULT 'instant',   -- 'instant' | 'form' | 'voice_transcript' | 'chat_transcript'
    agent_type  TEXT DEFAULT 'voice',     -- 'voice' | 'chat'
    lead_score  INTEGER DEFAULT 85,
    status      TEXT DEFAULT 'Interested', -- 'Deal Closed' | 'Interested' | 'Just Talked' | 'Review Later' | 'Not Interested'
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    role              TEXT NOT NULL,              -- 'user' | 'agent' | 'supervisor'
    content           TEXT NOT NULL,
    original_content  TEXT,
    corrected         INTEGER DEFAULT 0,          -- 1 if supervisor corrected
    correction_reason TEXT,
    quality_score     INTEGER DEFAULT 95,
    created_at        TEXT NOT NULL
);
"""


async def init_db() -> None:
    """Create tables on startup if they don't exist, and migrate missing columns."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_TABLES)

        # Auto-migrate missing columns for existing SQLite databases
        cursor = await db.execute("PRAGMA table_info(chat_messages)")
        cols = {row[1] for row in await cursor.fetchall()}
        
        if "original_content" not in cols:
            await db.execute("ALTER TABLE chat_messages ADD COLUMN original_content TEXT")
        if "correction_reason" not in cols:
            await db.execute("ALTER TABLE chat_messages ADD COLUMN correction_reason TEXT")
        if "quality_score" not in cols:
            await db.execute("ALTER TABLE chat_messages ADD COLUMN quality_score INTEGER DEFAULT 95")
        if "corrected" not in cols:
            await db.execute("ALTER TABLE chat_messages ADD COLUMN corrected INTEGER DEFAULT 0")

        cursor_leads = await db.execute("PRAGMA table_info(leads)")
        lead_cols = {row[1] for row in await cursor_leads.fetchall()}
        if "summary" not in lead_cols:
            await db.execute("ALTER TABLE leads ADD COLUMN summary TEXT")
        if "next_action" not in lead_cols:
            await db.execute("ALTER TABLE leads ADD COLUMN next_action TEXT")

        await db.commit()
    logger.info("SQLite database ready: %s", DB_PATH)


# ── Call Sessions ─────────────────────────────────────────────────────────────

async def save_session(call_id: str, agent_id: str) -> int:
    """Insert a new active session when a call begins."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO call_sessions (call_id, agent_id, status, started_at)
               VALUES (?, ?, 'active', ?)""",
            (call_id, agent_id, datetime.utcnow().isoformat()),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def end_session(
    call_id: str,
    duration: int | None,
    transcript: str | None,
) -> None:
    """Mark a session as ended and store duration + transcript."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE call_sessions
               SET status           = 'ended',
                   ended_at         = ?,
                   duration_seconds = ?,
                   transcript       = ?
               WHERE call_id = ?""",
            (datetime.utcnow().isoformat(), duration, transcript, call_id),
        )
        await db.commit()


async def get_sessions(limit: int = 20) -> list[dict]:
    """Return the most recent call sessions."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM call_sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def delete_session(session_id: int) -> bool:
    """Delete a session row. Returns True if a row was removed."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM call_sessions WHERE id = ?", (session_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


# ── Leads ─────────────────────────────────────────────────────────────────────

async def save_lead(
    name: str,
    email: str | None = None,
    phone: str | None = None,
    company: str | None = None,
    requirement: str | None = None,
    summary: str | None = None,
    next_action: str | None = None,
    source: str = "instant",
    agent_type: str = "voice",
    lead_score: int = 85,
    status: str = "Interested",
) -> dict:
    """Store a lead in SQLite with status classification and transcript summary."""
    created_at = datetime.utcnow().isoformat()
    safe_email = email or ""
    safe_phone = phone or ""
    safe_company = company or ""
    safe_requirement = requirement or ""
    safe_summary = summary or ""
    safe_next_action = next_action or ""
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO leads (name, email, phone, company, requirement, summary, next_action, source, agent_type, lead_score, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, safe_email, safe_phone, safe_company, safe_requirement, safe_summary, safe_next_action, source, agent_type, lead_score, status, created_at),
        )
        await db.commit()
        lead_id = cursor.lastrowid

    return {
        "id": lead_id,
        "name": name,
        "email": email,
        "phone": phone,
        "company": company,
        "requirement": requirement,
        "summary": summary,
        "next_action": next_action,
        "source": source,
        "agent_type": agent_type,
        "lead_score": lead_score,
        "status": status,
        "created_at": created_at,
    }


async def get_leads(limit: int = 50) -> list[dict]:
    """Return stored leads."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM leads ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ── Chat & Supervisor Logs ───────────────────────────────────────────────────

async def save_chat_message(
    session_id: str,
    role: str,
    content: str,
    original_content: str | None = None,
    corrected: bool = False,
    correction_reason: str | None = None,
    quality_score: int = 95,
) -> dict:
    """Store a message in SQLite."""
    created_at = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO chat_messages
               (session_id, role, content, original_content, corrected, correction_reason, quality_score, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                role,
                content,
                original_content,
                1 if corrected else 0,
                correction_reason,
                quality_score,
                created_at,
            ),
        )
        await db.commit()
        msg_id = cursor.lastrowid

    return {
        "id": msg_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "original_content": original_content,
        "corrected": corrected,
        "correction_reason": correction_reason,
        "quality_score": quality_score,
        "created_at": created_at,
    }


async def get_chat_history(session_id: str) -> list[dict]:
    """Return message history for a chat session."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_dashboard_stats() -> dict:
    """Return aggregated statistics for the admin dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Leads count
        cur = await db.execute("SELECT COUNT(*) as cnt FROM leads")
        row = await cur.fetchone()
        total_leads = row["cnt"] if row else 0

        # Calls count
        cur = await db.execute("SELECT COUNT(*) as cnt, AVG(duration_seconds) as avg_dur FROM call_sessions")
        row = await cur.fetchone()
        total_calls = row["cnt"] if row else 0
        avg_call_dur = int(row["avg_dur"]) if row and row["avg_dur"] else 0

        # Chat count & corrections
        cur = await db.execute("SELECT COUNT(*) as cnt, SUM(corrected) as corrections FROM chat_messages WHERE role = 'agent'")
        row = await cur.fetchone()
        total_chat_msgs = row["cnt"] if row else 0
        total_corrections = row["corrections"] if row and row["corrections"] else 0

        # Recent mistake logs
        cur = await db.execute(
            """SELECT * FROM chat_messages
               WHERE corrected = 1 ORDER BY id DESC LIMIT 10"""
        )
        corrections = [dict(r) for r in await cur.fetchall()]

        # Leads list
        cur = await db.execute("SELECT * FROM leads ORDER BY created_at DESC LIMIT 10")
        recent_leads = [dict(r) for r in await cur.fetchall()]

        return {
            "total_leads": total_leads,
            "total_calls": total_calls,
            "avg_call_duration": avg_call_dur,
            "total_chats": total_chat_msgs,
            "total_corrections": total_corrections,
            "ai_quality_scores": {
                "accuracy": 96,
                "empathy": 92,
                "sales_skills": 95,
                "grammar": 100,
                "compliance": 99,
            },
            "recent_corrections": corrections,
            "recent_leads": recent_leads,
        }
