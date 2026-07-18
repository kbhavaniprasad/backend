"""
LearningGenerator — converts EvaluationReport mistakes into actionable
Learning records and applies them to Agent A's prompt via its REST API.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from openai import AsyncOpenAI
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.models.evaluation import (
    EvaluationReport,
    Learning,
    LearningStatus,
    Mistake,
    SeverityLevel,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_COLLECTION = "learnings"

# Mistake severities that automatically generate learnings
_AUTO_GENERATE_SEVERITIES = {SeverityLevel.critical, SeverityLevel.high}

# Minimum confidence before auto-applying a learning
_AUTO_APPLY_CONFIDENCE_THRESHOLD = settings.high_confidence_threshold

# ---------------------------------------------------------------------------
# GPT-4o prompt for learning generation
# ---------------------------------------------------------------------------

_LEARNING_SYSTEM_PROMPT = """
You are an expert AI trainer that generates precise, actionable corrections
for AI sales agents. Given a specific mistake made by an AI sales agent,
you must produce:

1. A clear title for the correction
2. A category label (e.g. pricing, objection_handling, qualification, tone, etc.)
3. A description of the learning
4. old_behavior: Concise description of the problematic behaviour
5. new_behavior: Concise description of the correct behaviour
6. correction_prompt_snippet: The EXACT text to inject into the agent's system
   prompt to enforce the correct behaviour. This must be directive and specific,
   starting with "IMPORTANT:" or "RULE:" to ensure Agent A gives it priority.
7. confidence_score: Your confidence (0-1) that this correction will fix the issue

Return ONLY valid JSON (no markdown fences):
{
  "title": "<string>",
  "category": "<string>",
  "description": "<string>",
  "old_behavior": "<string>",
  "new_behavior": "<string>",
  "correction_prompt_snippet": "<string>",
  "confidence_score": <float 0-1>
}
""".strip()


class LearningGenerator:
    """
    Generates Learning records from EvaluationReport mistakes and manages
    the lifecycle of applying / rolling back those learnings.
    """

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        kafka_producer,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._db = db
        self._producer = kafka_producer
        self._openai = AsyncOpenAI(api_key=settings.openai_api_key)
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _generate_correction(self, mistake: Mistake) -> Dict[str, Any]:
        """Use GPT-4o to generate a structured correction for a single mistake."""
        user_prompt = (
            f"Mistake type: {mistake.type.value}\n"
            f"Severity: {mistake.severity.value}\n"
            f"Description: {mistake.description}\n"
            f"Context/Recommended correction: {mistake.recommended_correction}\n\n"
            "Generate a precise correction and prompt snippet for this mistake."
        )

        response = await self._openai.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": _LEARNING_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("JSON parse error generating correction: %s", exc)
            return {}

    async def _persist_learning(self, learning: Learning) -> Learning:
        """Insert a Learning document into MongoDB and return it with its id."""
        doc = learning.model_dump(by_alias=True, exclude={"id"})
        result = await self._db[_COLLECTION].insert_one(doc)
        learning.id = str(result.inserted_id)
        logger.info("Learning persisted: %s (%s)", learning.id, learning.title)
        return learning

    async def _update_learning_status(
        self,
        learning_id: str,
        status: LearningStatus,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update the status and optional extra fields of a learning document."""
        from bson import ObjectId

        update: Dict[str, Any] = {
            "status": status.value,
            "updated_at": datetime.utcnow(),
        }
        if extra_fields:
            update.update(extra_fields)

        await self._db[_COLLECTION].update_one(
            {"_id": ObjectId(learning_id)},
            {"$set": update},
        )

    async def _fetch_learning_by_id(self, learning_id: str) -> Optional[Learning]:
        """Fetch a single Learning document from MongoDB."""
        from bson import ObjectId

        doc = await self._db[_COLLECTION].find_one({"_id": ObjectId(learning_id)})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return Learning(**doc)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def generate_learnings(
        self, evaluation: EvaluationReport
    ) -> List[Learning]:
        """
        Generate Learning records for each critical or high-severity mistake
        found in the given EvaluationReport.

        Args:
            evaluation: The EvaluationReport to derive learnings from.

        Returns:
            List of persisted Learning instances.
        """
        learnings: List[Learning] = []

        eligible_mistakes = [
            m for m in evaluation.mistakes if m.severity in _AUTO_GENERATE_SEVERITIES
        ]

        logger.info(
            "Generating learnings for evaluation %s — %d eligible mistakes",
            evaluation.id,
            len(eligible_mistakes),
        )

        for mistake in eligible_mistakes:
            correction = await self._generate_correction(mistake)

            if not correction:
                logger.warning(
                    "Skipping mistake %s — no correction generated", mistake.type
                )
                continue

            learning = Learning(
                tenant_id=evaluation.tenant_id,
                source_evaluation_id=str(evaluation.id),
                category=correction.get("category", mistake.type.value),
                title=correction.get("title", f"Fix: {mistake.type.value}"),
                description=correction.get("description", mistake.description),
                old_behavior=correction.get("old_behavior", mistake.description),
                new_behavior=correction.get(
                    "new_behavior", mistake.recommended_correction
                ),
                correction_prompt_snippet=correction.get(
                    "correction_prompt_snippet",
                    f"RULE: {mistake.recommended_correction}",
                ),
                confidence_score=float(correction.get("confidence_score", 0.5)),
                severity=mistake.severity,
                status=LearningStatus.pending,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

            learning = await self._persist_learning(learning)
            learnings.append(learning)

        logger.info(
            "Generated %d learnings for evaluation %s",
            len(learnings),
            evaluation.id,
        )
        return learnings

    async def apply_learning(
        self,
        learning: Learning,
        agent_a_service_url: str,
    ) -> bool:
        """
        Apply a learning by creating a new prompt version in Agent A service.

        Steps:
          1. POST to Agent A's /api/v1/agent/prompt-versions with the
             correction_prompt_snippet.
          2. On success, update learning.status = 'applied'.

        Args:
            learning:           The Learning to apply.
            agent_a_service_url: Base URL of the Agent A service.

        Returns:
            True if applied successfully, False otherwise.
        """
        url = f"{agent_a_service_url}/api/v1/agent/prompt-versions"
        payload = {
            "tenant_id": learning.tenant_id,
            "learning_id": learning.id,
            "correction_snippet": learning.correction_prompt_snippet,
            "category": learning.category,
            "severity": learning.severity.value,
            "source_evaluation_id": learning.source_evaluation_id,
        }

        logger.info(
            "Applying learning %s to Agent A at %s", learning.id, url
        )

        try:
            response = await self._http.post(url, json=payload)
            response.raise_for_status()

            await self._update_learning_status(
                str(learning.id),
                LearningStatus.applied,
                extra_fields={"applied_at": datetime.utcnow()},
            )

            # Publish Kafka event
            await self._producer.publish(
                topic=settings.kafka_topic_learning_applied,
                key=str(learning.id),
                value={
                    "event_type": "learning.applied",
                    "learning_id": learning.id,
                    "tenant_id": learning.tenant_id,
                    "category": learning.category,
                    "severity": learning.severity.value,
                    "applied_at": datetime.utcnow().isoformat(),
                },
            )

            logger.info("Learning %s applied successfully", learning.id)
            return True

        except httpx.HTTPStatusError as exc:
            logger.error(
                "HTTP error applying learning %s: %s — %s",
                learning.id,
                exc.response.status_code,
                exc.response.text,
            )
        except Exception as exc:
            logger.error("Unexpected error applying learning %s: %s", learning.id, exc)

        # Mark as failed
        await self._update_learning_status(str(learning.id), LearningStatus.pending)
        return False

    async def rollback_learning(
        self, learning_id: str, reason: str
    ) -> bool:
        """
        Roll back a previously applied learning by restoring the prior prompt
        version in Agent A service and updating this learning's status.

        Args:
            learning_id: MongoDB ID of the Learning to roll back.
            reason:      Human-readable reason for the rollback.

        Returns:
            True if rolled back successfully, False otherwise.
        """
        learning = await self._fetch_learning_by_id(learning_id)
        if not learning:
            logger.error("Learning %s not found for rollback", learning_id)
            return False

        if learning.status != LearningStatus.applied:
            logger.warning(
                "Cannot rollback learning %s — current status: %s",
                learning_id,
                learning.status,
            )
            return False

        url = (
            f"{settings.agent_a_service_url}/api/v1/agent/prompt-versions/rollback"
        )
        payload = {
            "tenant_id": learning.tenant_id,
            "learning_id": learning_id,
            "reason": reason,
        }

        logger.info("Rolling back learning %s — reason: %s", learning_id, reason)

        try:
            response = await self._http.post(url, json=payload)
            response.raise_for_status()

            await self._update_learning_status(
                learning_id,
                LearningStatus.rolled_back,
                extra_fields={"rollback_reason": reason},
            )

            logger.info("Learning %s rolled back successfully", learning_id)
            return True

        except httpx.HTTPStatusError as exc:
            logger.error(
                "HTTP error rolling back learning %s: %s — %s",
                learning_id,
                exc.response.status_code,
                exc.response.text,
            )
        except Exception as exc:
            logger.error(
                "Unexpected error rolling back learning %s: %s", learning_id, exc
            )

        return False
