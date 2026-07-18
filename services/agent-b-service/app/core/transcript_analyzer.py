"""
TranscriptAnalyzer — uses GPT-4o to perform deep qualitative analysis
of sales conversation transcripts on behalf of Agent B (AI Manager).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_EVALUATION_SYSTEM_PROMPT = """
You are a strict, expert sales conversation quality auditor for an AI-powered
lead engagement platform. Your role is to evaluate AI sales agent conversations
and identify areas for improvement with precision and rigor.

Evaluate conversations across these dimensions:
1. **Factual Accuracy** – Did the agent quote correct product details, pricing,
   features, and policies? Any factual error must be flagged.
2. **Qualification** – Did the agent ask the right discovery questions to properly
   qualify the lead (budget, authority, need, timeline)?
3. **Upselling / Cross-Selling** – Were relevant upgrade or add-on opportunities
   presented at the right moment?
4. **Objection Handling** – Did the agent address objections confidently and
   empathetically, or did it avoid/mishandle them?
5. **Tone & Professionalism** – Was the agent polite, professional, and
   appropriately empathetic throughout?
6. **Booking Attempts** – Did the agent actively attempt to schedule a follow-up
   meeting or demo when appropriate?
7. **FAQ Accuracy** – Were answers to common questions accurate and complete?
8. **Conversation Length** – Was the conversation appropriately concise without
   being abrupt, or unnecessarily verbose?

Scoring guidelines:
- 9-10: Exceptional, near-perfect execution
- 7-8: Good performance with minor issues
- 5-6: Average, notable gaps in quality
- 3-4: Poor, significant mistakes
- 0-2: Unacceptable, fundamental failures

Return ONLY valid JSON matching this schema (no markdown fences):
{
  "overall_score": <float 0-10>,
  "qualification_accuracy": <float 0-1>,
  "sentiment_trend": "improving" | "stable" | "declining",
  "strengths": [<string>, ...],
  "missed_opportunities": [<string>, ...],
  "coaching_feedback": "<detailed narrative feedback string>",
  "mistakes": [
    {
      "type": "<MistakeType enum value>",
      "description": "<what went wrong>",
      "timestamp_in_call": <seconds as float>,
      "recommended_correction": "<what should have been said/done>",
      "severity": "critical" | "high" | "medium" | "low" | "info",
      "confidence_score": <float 0-1>
    }
  ]
}

Valid mistake types: wrong_information, missed_upsell, missed_qualification,
hallucination, poor_tone, incorrect_pricing, missed_objection_handling,
too_long, too_short, other
""".strip()

_HALLUCINATION_SYSTEM_PROMPT = """
You are a factual verification specialist for AI sales agent conversations.
Your task is to identify any factual claims in the transcript that are NOT
supported by or contradict the provided knowledge base context.

Examine each factual claim (pricing, feature names, availability, policies,
deadlines, guarantees) and flag hallucinations.

Return ONLY valid JSON (no markdown fences):
{
  "hallucinations": [
    {
      "claim": "<exact quote from transcript>",
      "issue": "<why this is incorrect or unsupported>",
      "timestamp_in_call": <seconds as float>,
      "confidence_score": <float 0-1>
    }
  ]
}

If no hallucinations are found, return: {"hallucinations": []}
""".strip()


# ---------------------------------------------------------------------------
# TranscriptAnalyzer class
# ---------------------------------------------------------------------------

class TranscriptAnalyzer:
    """
    Wraps GPT-4o calls to produce structured evaluation data from raw
    conversation transcripts.
    """

    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_conversation_text(self, conversation: Dict[str, Any]) -> str:
        """Convert conversation dict into a human-readable transcript string."""
        messages: List[Dict] = conversation.get("messages", [])
        lines: List[str] = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            timestamp = msg.get("timestamp_seconds", 0.0)
            lines.append(f"[{timestamp:.1f}s] {role}: {content}")
        return "\n".join(lines) if lines else conversation.get("transcript_text", "")

    def _build_tenant_context_text(self, tenant_context: Dict[str, Any]) -> str:
        """Serialise tenant context metadata to inject into the prompt."""
        return json.dumps(tenant_context, indent=2, default=str)

    async def _chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        """Execute a chat completion and return the raw string response."""
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"

    def _safe_parse_json(self, raw: str, context: str = "") -> Dict[str, Any]:
        """Parse JSON and log/recover from errors gracefully."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("JSON parse error in %s: %s — raw: %.300s", context, exc, raw)
            return {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def analyze(
        self,
        conversation: Dict[str, Any],
        tenant_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Perform a comprehensive qualitative analysis of a conversation transcript
        using GPT-4o acting as a strict sales quality auditor.

        Args:
            conversation:   Full conversation document from lead-service / Kafka event.
            tenant_context: Business context (product info, pricing, tenant settings).

        Returns:
            Structured evaluation dict matching the EvaluationReport fields.
        """
        transcript_text = self._build_conversation_text(conversation)
        context_text = self._build_tenant_context_text(tenant_context)

        user_prompt = (
            f"## TENANT / BUSINESS CONTEXT\n{context_text}\n\n"
            f"## CONVERSATION TRANSCRIPT\n{transcript_text}\n\n"
            "Evaluate the conversation according to your instructions and "
            "return the structured JSON evaluation."
        )

        logger.info(
            "Analyzing conversation %s for tenant %s",
            conversation.get("conversation_id"),
            conversation.get("tenant_id"),
        )

        raw = await self._chat_completion(
            system_prompt=_EVALUATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        result = self._safe_parse_json(raw, context="transcript_analyzer.analyze")

        # Apply safe defaults so callers don't need to guard every key
        result.setdefault("overall_score", 5.0)
        result.setdefault("qualification_accuracy", 0.5)
        result.setdefault("sentiment_trend", "stable")
        result.setdefault("strengths", [])
        result.setdefault("missed_opportunities", [])
        result.setdefault("coaching_feedback", "No coaching feedback generated.")
        result.setdefault("mistakes", [])

        logger.info(
            "Analysis complete for conversation %s — score: %.1f, mistakes: %d",
            conversation.get("conversation_id"),
            result["overall_score"],
            len(result["mistakes"]),
        )
        return result

    async def detect_hallucinations(
        self,
        transcript: str,
        knowledge_base_context: str,
    ) -> List[Dict[str, Any]]:
        """
        Specifically scan the transcript for factual claims that contradict or
        are unsupported by the knowledge base context.

        Args:
            transcript:             Plain-text transcript of the conversation.
            knowledge_base_context: Relevant knowledge base excerpts (FAQ, pricing,
                                    feature list) retrieved for this tenant.

        Returns:
            List of hallucination dicts with claim, issue, timestamp, confidence_score.
        """
        user_prompt = (
            f"## KNOWLEDGE BASE (source of truth)\n{knowledge_base_context}\n\n"
            f"## TRANSCRIPT TO VERIFY\n{transcript}\n\n"
            "Identify any hallucinations or unsupported factual claims. "
            "Return structured JSON as instructed."
        )

        logger.info("Running hallucination detection on transcript (%.80s…)", transcript)

        raw = await self._chat_completion(
            system_prompt=_HALLUCINATION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
        )
        result = self._safe_parse_json(raw, context="detect_hallucinations")
        hallucinations: List[Dict[str, Any]] = result.get("hallucinations", [])

        logger.info("Hallucination check complete — found %d issues", len(hallucinations))
        return hallucinations
