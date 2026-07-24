"""
config.py — Environment configuration
All settings come from .env — nothing is hardcoded.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (parent of backend/)
load_dotenv(Path(__file__).parent.parent / ".env")


class Config:
    # ── Retell AI ────────────────────────────────────────────────────────────
    RETELL_API_KEY: str = os.getenv("RETELL_API_KEY", "")
    RETELL_AGENT_ID: str = os.getenv("RETELL_AGENT_ID", "")
    RETELL_BASE_URL: str = os.getenv("RETELL_BASE_URL", "https://api.retellai.com")

    # ── Agent 2 (Supervisor / Evaluator AI) ──────────────────────────────────
    RETELL_SUPERVISOR_API_KEY: str = os.getenv("RETELL_SUPERVISOR_API_KEY", "key_54a855ae72a7c2af24c55ab8b993")
    RETELL_SUPERVISOR_AGENT_ID: str = os.getenv("RETELL_SUPERVISOR_AGENT_ID", "agent_b81bb76fd17b39114462c04b5f")

    # ── Server ────────────────────────────────────────────────────────────────
    PORT: int = int(os.getenv("PORT", "8000"))
    HOST: str = os.getenv("HOST", "0.0.0.0")

    # ── CORS ──────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: list[str] = os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000"
    ).split(",")

    # ── Database ─────────────────────────────────────────────────────────────
    DB_PATH: str = os.getenv("DB_PATH", "voice_agent.db")

    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "20"))
    RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))


    # ── AI / Supervisor ───────────────────────────────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    SUPERVISOR_ENABLED: bool = os.getenv("SUPERVISOR_ENABLED", "true").lower() == "true"


config = Config()
