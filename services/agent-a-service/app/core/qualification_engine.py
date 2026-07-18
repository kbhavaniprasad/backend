"""
LeadQualificationEngine — GPT-4o powered lead scoring and meeting extraction.

Analyses full conversation transcripts to determine whether a lead meets the
BANT (Budget / Authority / Need / Timeline) qualification criteria, assigns a
score, and extracts any proposed meeting details.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Qualification criteria definitions
# ─────────────────────────────────────────────────────────────────────────────

QUALIFICATION_CRITERIA: dict[str, str] = {
    "has_budget": (
        "The lead has indicated they have an allocated or approximate budget for "
        "the product/service.  They may have mentioned a monetary figure, a range, "
        "or confirmed they are funded for this purchase."
    ),
    "has_timeline": (
        "The lead has expressed a concrete or approximate timeframe for when they "
        "intend to purchase or implement the solution (e.g., 'within Q3', "
        "'next month', 'ASAP', 'within 6 months')."
    ),
    "is_decision_maker": (
        "The lead is the primary decision-maker or has significant influence over "
        "the purchasing decision.  They can approve the purchase without requiring "
        "approval from a superior."
    ),
    "has_specific_need": (
        "The lead has articulated a specific, well-defined problem or requirement "
        "that the business's product or service can directly address.  Vague "
        "interest does not satisfy this criterion."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# JSON response schema (passed to GPT as a structured prompt)
# ─────────────────────────────────────────────────────────────────────────────

_QUALIFICATION_SCHEMA = {
    "is_qualified": "boolean — true if the lead meets at least 3 out of 4 criteria",
    "score": "float between 0.0 and 1.0 — overall qualification score",
    "criteria": {
        "has_budget": "boolean",
        "has_timeline": "boolean",
        "is_decision_maker": "boolean",
        "has_specific_need": "boolean",
    },
    "reasoning": "string — concise explanation of the score and which criteria were met/unmet",
    "recommended_action": (
        "string — one of: 'book_meeting', 'nurture', 'disqualify', 'escalate_to_human'"
    ),
}

_MEETING_EXTRACTION_SCHEMA = {
    "proposed_date": "string — ISO 8601 date or natural language date (e.g., 'next Tuesday')",
    "proposed_time": "string — time in HH:MM format or natural language (e.g., '3 PM')",
    "meeting_type": "string — one of: 'video_call', 'phone_call', 'in_person'",
    "timezone": "string — IANA timezone identifier if mentioned (e.g., 'America/New_York')",
    "duration_minutes": "integer — estimated duration in minutes (default 30)",
    "notes": "string — any additional context mentioned about the meeting",
}


class LeadQualificationEngine:
    """
    Uses GPT-4o to analyse conversation transcripts and qualify leads.

    Example::

        engine = LeadQualificationEngine(openai_client)
        result = await engine.qualify_lead(transcript, lead_data)
        print(result["is_qualified"], result["score"])
    """

    def __init__(self, openai_client: AsyncOpenAI, model: str = "gpt-4o") -> None:
        self._client = openai_client
        self._model = model

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def qualify_lead(
        self,
        conversation_transcript: str,
        lead_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Analyse *conversation_transcript* and return a structured qualification result.

        Args:
            conversation_transcript: Full plain-text transcript of the conversation.
            lead_data:               Dict with lead metadata (name, email, company, source…).

        Returns:
            Dict with keys: is_qualified (bool), score (float), criteria (dict),
            reasoning (str), recommended_action (str).
        """
        if not conversation_transcript.strip():
            logger.warning("qualify_lead called with empty transcript — returning unqualified.")
            return self._default_unqualified("Empty transcript provided.")

        criteria_descriptions = "\n".join(
            f"  - {key}: {desc}" for key, desc in QUALIFICATION_CRITERIA.items()
        )

        system_prompt = (
            "You are an expert lead qualification analyst.  Analyse the conversation "
            "transcript provided by the user and evaluate the lead against the BANT "
            "qualification criteria listed below.\n\n"
            "QUALIFICATION CRITERIA:\n"
            f"{criteria_descriptions}\n\n"
            "SCORING RULES:\n"
            "- Each satisfied criterion contributes 0.25 to the score.\n"
            "- is_qualified = true when score >= 0.75 (at least 3 criteria met).\n"
            "- recommended_action mapping:\n"
            "    score >= 0.75 → 'book_meeting'\n"
            "    0.50 <= score < 0.75 → 'nurture'\n"
            "    score < 0.50 → 'disqualify'\n"
            "  Use 'escalate_to_human' only when the lead explicitly requests a human.\n\n"
            "Respond ONLY with a valid JSON object matching this schema (no markdown):\n"
            f"{json.dumps(_QUALIFICATION_SCHEMA, indent=2)}"
        )

        lead_info = (
            f"Lead Name: {lead_data.get('name', 'Unknown')}\n"
            f"Company:   {lead_data.get('company', 'N/A')}\n"
            f"Source:    {lead_data.get('source', 'N/A')}\n"
        )

        user_prompt = (
            f"LEAD INFORMATION:\n{lead_info}\n\n"
            f"CONVERSATION TRANSCRIPT:\n{conversation_transcript}"
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,  # low temperature for consistent, analytical output
                max_tokens=512,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content or "{}"
            result: dict[str, Any] = json.loads(raw)

            # Validate and coerce types for downstream consumers
            result = self._coerce_qualification_result(result)
            logger.info(
                "Lead qualified | score=%.2f is_qualified=%s action=%s",
                result["score"],
                result["is_qualified"],
                result["recommended_action"],
            )
            return result

        except json.JSONDecodeError as exc:
            logger.error("Failed to parse qualification JSON: %s", exc)
            return self._default_unqualified("JSON parse error from LLM response.")
        except Exception as exc:
            logger.error("LeadQualificationEngine.qualify_lead error: %s", exc, exc_info=True)
            return self._default_unqualified(str(exc))

    async def extract_meeting_info(
        self,
        transcript: str,
    ) -> dict[str, Any] | None:
        """
        Scan *transcript* for a proposed meeting date, time, and type.

        Returns:
            A dict matching ``_MEETING_EXTRACTION_SCHEMA`` if a meeting proposal is found,
            or ``None`` if no meeting proposal is detected.
        """
        if not transcript.strip():
            return None

        # Quick heuristic: only invoke GPT if meeting-related keywords are present
        meeting_keywords = re.compile(
            r"\b(schedule|book|meeting|call|appointment|calendar|available|slot|time|date)\b",
            re.IGNORECASE,
        )
        if not meeting_keywords.search(transcript):
            logger.debug("No meeting keywords found; skipping extraction.")
            return None

        system_prompt = (
            "You are an assistant that extracts proposed meeting details from conversation "
            "transcripts.  If the transcript contains a proposed or agreed meeting, extract "
            "the details.  If no concrete meeting proposal exists, return null.\n\n"
            "Respond ONLY with a valid JSON object matching this schema OR the literal null:\n"
            f"{json.dumps(_MEETING_EXTRACTION_SCHEMA, indent=2)}"
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"TRANSCRIPT:\n{transcript}"},
                ],
                temperature=0.0,
                max_tokens=256,
                response_format={"type": "json_object"},
            )

            raw = (response.choices[0].message.content or "").strip()
            if raw.lower() in {"null", "none", ""}:
                return None

            meeting: dict[str, Any] = json.loads(raw)

            # If GPT returned an empty shell, treat as no meeting
            if not meeting.get("proposed_date") and not meeting.get("proposed_time"):
                return None

            logger.info("Meeting info extracted: %s", meeting)
            return meeting

        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse meeting extraction JSON: %s", exc)
            return None
        except Exception as exc:
            logger.error("extract_meeting_info error: %s", exc, exc_info=True)
            return None

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _default_unqualified(reason: str) -> dict[str, Any]:
        """Return a safe default unqualified result."""
        return {
            "is_qualified": False,
            "score": 0.0,
            "criteria": {k: False for k in QUALIFICATION_CRITERIA},
            "reasoning": reason,
            "recommended_action": "nurture",
        }

    @staticmethod
    def _coerce_qualification_result(raw: dict[str, Any]) -> dict[str, Any]:
        """
        Coerce LLM output into the expected types, filling defaults for missing fields.
        """
        criteria = raw.get("criteria", {})
        if not isinstance(criteria, dict):
            criteria = {}

        # Ensure all criteria keys are present and boolean
        coerced_criteria = {
            key: bool(criteria.get(key, False)) for key in QUALIFICATION_CRITERIA
        }

        score = float(raw.get("score", 0.0))
        score = max(0.0, min(1.0, score))  # clamp to [0, 1]

        valid_actions = {"book_meeting", "nurture", "disqualify", "escalate_to_human"}
        action = raw.get("recommended_action", "nurture")
        if action not in valid_actions:
            action = "nurture"

        return {
            "is_qualified": bool(raw.get("is_qualified", False)),
            "score": score,
            "criteria": coerced_criteria,
            "reasoning": str(raw.get("reasoning", "")),
            "recommended_action": action,
        }
