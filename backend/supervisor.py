"""
supervisor.py — Agent 2 (Supervisor / Evaluator AI)
Monitors Sales AI Agent responses in real-time.
Evaluates accuracy, tone, compliance, and suggests or applies corrections.
Supports OpenAI GPT or smart fallback evaluation.
"""

from __future__ import annotations

import logging
import re
from typing import TypedDict

import httpx

from config import config

logger = logging.getLogger(__name__)


class EvaluationResult(TypedDict):
    is_correct: bool
    corrected_response: str
    original_response: str
    reason: str | None
    quality_score: int


# Knowledge base used for rule-based supervisor checking and system prompts
KNOWLEDGE_BASE = """
Company: AI Lead Engagement Platform (Nova)
Capabilities:
- WhatsApp Business API Integration: Supported out-of-the-box.
- Cancellation Policy: Cancel anytime, zero long-term contracts.
- Pricing: Starter ($49/mo), Pro ($149/mo), Enterprise (Custom).
- Free Trial: 14 days free trial with full feature access.
- Voice & Chat: Instant WebRTC AI Voice call + Real-time Socket/SSE Live Chat.
- Supervisor AI: Dual-agent system with real-time quality monitoring.
"""

FALLBACK_RULES = [
    {
        "keywords": ["whatsapp"],
        "trigger": lambda msg: any(w in msg.lower() for w in ["no", "don't support", "not support", "cannot"]),
        "correction": "Yes! We fully support WhatsApp integration using the WhatsApp Business API. Would you like a demo?",
        "reason": "Corrected inaccurate negative answer about WhatsApp integration support."
    },
    {
        "keywords": ["cancel", "contract"],
        "trigger": lambda msg: any(w in msg.lower() for w in ["don't know", "not sure", "depends", "maybe"]),
        "correction": "Yes, you can cancel anytime. There are no long-term contracts or hidden cancellation fees.",
        "reason": "Clarified clear, customer-friendly cancellation policy."
    },
    {
        "keywords": ["price", "cost", "pricing"],
        "trigger": lambda msg: any(w in msg.lower() for w in ["expensive", "secret", "don't know", "free only"]),
        "correction": "Our plans start at $49/mo for Starter and $149/mo for Pro, with a 14-day free trial. Custom enterprise plans are also available!",
        "reason": "Provided precise transparent pricing information."
    },
    {
        "keywords": ["human", "agent", "sales rep"],
        "trigger": lambda msg: any(w in msg.lower() for w in ["no human", "bot only", "cannot talk to human"]),
        "correction": "Our AI handles initial qualification, but I can seamlessly connect or book a meeting with a human specialist whenever you prefer!",
        "reason": "Ensured accurate policy on human escalation capability."
    }
]


async def evaluate_response(
    user_message: str,
    agent_response: str,
) -> EvaluationResult:
    """
    Evaluate Sales Agent's response using OpenAI GPT if configured,
    or smart rule-based evaluator fallback.
    """
    if not config.SUPERVISOR_ENABLED:
        return {
            "is_correct": True,
            "corrected_response": agent_response,
            "original_response": agent_response,
            "reason": None,
            "quality_score": 98,
        }

    # Attempt OpenAI evaluation if key is available
    if config.OPENAI_API_KEY:
        try:
            return await _evaluate_with_openai(user_message, agent_response)
        except Exception as exc:
            logger.warning("OpenAI Supervisor evaluation failed, using rule engine: %s", exc)

    # Fallback to Rule Engine Evaluation
    return _evaluate_with_rules(user_message, agent_response)


async def _evaluate_with_openai(
    user_message: str,
    agent_response: str,
) -> EvaluationResult:
    """Call OpenAI GPT-4o-mini to inspect conversation quality."""
    prompt = f"""
You are Agent 2 (Supervisor / Evaluator AI). Your job is to monitor Agent 1 (Sales AI) and catch mistakes, wrong pricing, hallucinations, or unhelpful answers.

Knowledge Base:
{KNOWLEDGE_BASE}

Customer message: "{user_message}"
Sales Agent response: "{agent_response}"

Evaluate:
1. Is the sales agent response accurate, compliant, polite, and helpful?
2. If wrong, unhelpful, or saying "I don't know", provide the corrected response and reason.

Respond ONLY in JSON format with keys:
"is_correct": boolean,
"corrected_response": string,
"reason": string or null,
"quality_score": integer (0-100)
"""

    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "system", "content": prompt}],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        import json
        res = json.loads(content)

        return {
            "is_correct": res.get("is_correct", True),
            "corrected_response": res.get("corrected_response", agent_response),
            "original_response": agent_response,
            "reason": res.get("reason"),
            "quality_score": res.get("quality_score", 95),
        }


def _evaluate_with_rules(
    user_message: str,
    agent_response: str,
) -> EvaluationResult:
    """Rule-based evaluator for instant execution without extra dependencies."""
    user_msg_lower = user_message.lower()
    
    # Catch common phrases like "I don't know", "Not sure"
    if any(p in agent_response.lower() for p in ["i don't know", "i'm not sure", "no idea"]):
        if "whatsapp" in user_msg_lower:
            return {
                "is_correct": False,
                "corrected_response": "Yes! We support WhatsApp integration using the WhatsApp Business API. Would you like a live demo?",
                "original_response": agent_response,
                "reason": "Agent expressed uncertainty about WhatsApp integration.",
                "quality_score": 60,
            }
        elif "cancel" in user_msg_lower or "contract" in user_msg_lower:
            return {
                "is_correct": False,
                "corrected_response": "Yes, you can cancel anytime. There are no long-term contracts.",
                "original_response": agent_response,
                "reason": "Agent did not provide clear cancellation terms.",
                "quality_score": 65,
            }

    for rule in FALLBACK_RULES:
        if any(kw in user_msg_lower for kw in rule["keywords"]):
            if rule["trigger"](agent_response):
                return {
                    "is_correct": False,
                    "corrected_response": rule["correction"],
                    "original_response": agent_response,
                    "reason": rule["reason"],
                    "quality_score": 60,
                }

    return {
        "is_correct": True,
        "corrected_response": agent_response,
        "original_response": agent_response,
        "reason": None,
        "quality_score": 98,
    }


async def analyze_transcript_and_classify_lead(
    transcript: str,
    agent_type: str = "voice",
) -> dict:
    """
    Post-Call / Post-Chat Analysis by Agent 2 (Supervisor AI).
    Takes full transcript after call/chat ends, analyzes user intent,
    scores quality/interest, and classifies lead status:
    - 'Deal Closed': User committed, signed up, or agreed to buy
    - 'Interested': High interest, asked for demo, pricing, or details
    - 'Just Talked': General inquiry, low immediate intent
    - 'Review Later': Enterprise request, custom pricing, or follow-up needed
    - 'Not Interested': Explicitly declined
    """
    if not transcript or not transcript.strip():
        return {
            "status": "Just Talked",
            "lead_score": 50,
            "summary": "No transcript available.",
            "requirement": "General inquiry",
            "next_action": "Follow up if user reaches out.",
        }

    # If OpenAI API Key is available, use GPT for deep analysis
    if config.OPENAI_API_KEY:
        try:
            return await _analyze_transcript_with_openai(transcript, agent_type)
        except Exception as exc:
            logger.warning("OpenAI transcript analysis failed, using rule classifier: %s", exc)

    # Rule-Based Intelligent Classifier Fallback
    text = transcript.lower()
    
    # 1. Closed Deal keywords
    if any(k in text for k in ["deal closed", "signed up", "purchased", "bought", "subscribed", "ready to start", "send invoice"]):
        status = "Deal Closed"
        score = 95
        next_action = "Onboard customer and setup account."
    # 2. High Interest keywords
    elif any(k in text for k in ["pricing", "cost", "demo", "schedule", "integration", "whatsapp", "starter", "pro"]):
        status = "Interested"
        score = 85
        next_action = "Send follow-up demo link & pricing proposal."
    # 3. Enterprise / Custom Review keywords
    elif any(k in text for k in ["enterprise", "custom", "security", "sla", "contract", "legal", "review later", "think about it"]):
        status = "Review Later"
        score = 70
        next_action = "Schedule follow-up call with Account Executive."
    # 4. Not Interested keywords
    elif any(k in text for k in ["not interested", "don't want", "too expensive", "no thanks", "stop"]):
        status = "Not Interested"
        score = 25
        next_action = "Archive lead."
    # 5. Default
    else:
        status = "Just Talked"
        score = 60
        next_action = "Send standard nurture email."

    summary_snippet = transcript[:250].replace("\n", " ").strip() + "..."

    return {
        "status": status,
        "lead_score": score,
        "summary": f"Agent 2 Analysis ({agent_type.capitalize()}): {summary_snippet}",
        "requirement": f"User engaged via {agent_type} agent.",
        "next_action": next_action,
    }


async def _analyze_transcript_with_openai(transcript: str, agent_type: str) -> dict:
    """Call OpenAI GPT-4o-mini to perform end-of-call/chat transcript analysis."""
    prompt = f"""
You are Agent 2 (Supervisor AI). Your task is to analyze the full {agent_type} interaction transcript after the session has ended.

Transcript:
\"\"\"
{transcript}
\"\"\"

Analyze and extract:
1. "status": Must be EXACTLY one of: ["Deal Closed", "Interested", "Just Talked", "Review Later", "Not Interested"]
2. "lead_score": Integer from 0 to 100 representing lead quality/intent.
3. "summary": Concise 2-sentence summary of the conversation.
4. "requirement": What the user specifically asked for or needed.
5. "next_action": Recommended next step for the sales/support team.

Respond ONLY in valid JSON format matching these keys.
"""

    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "system", "content": prompt}],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        import json
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
        
        return {
            "status": data.get("status", "Interested"),
            "lead_score": data.get("lead_score", 80),
            "summary": data.get("summary", transcript[:200]),
            "requirement": data.get("requirement", f"{agent_type.capitalize()} interaction"),
            "next_action": data.get("next_action", "Follow up with lead"),
        }

