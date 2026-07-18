"""
PerformanceEvaluator — orchestrates the full evaluation pipeline for a
completed Agent A conversation, storing results and publishing Kafka events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.core.transcript_analyzer import TranscriptAnalyzer
from app.models.evaluation import (
    EvaluationReport,
    ImprovementStatus,
    Mistake,
    MistakeType,
    SentimentTrend,
    SeverityLevel,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_COLLECTION = "evaluation_reports"


class PerformanceEvaluator:
    """
    Orchestrates the full evaluation pipeline:
      1. Transcript analysis via TranscriptAnalyzer (GPT-4o)
      2. Hallucination detection
      3. EvaluationReport construction and persistence
      4. Kafka event publishing
    """

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        kafka_producer,
        transcript_analyzer: Optional[TranscriptAnalyzer] = None,
    ) -> None:
        self._db = db
        self._producer = kafka_producer
        self._analyzer = transcript_analyzer or TranscriptAnalyzer()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_tenant_context(self, conversation_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract tenant context dict from the conversation event payload."""
        return {
            "tenant_id": conversation_data.get("tenant_id", ""),
            "product_name": conversation_data.get("product_name", ""),
            "pricing": conversation_data.get("pricing", {}),
            "features": conversation_data.get("features", []),
            "faq_context": conversation_data.get("faq_context", ""),
            "agent_instructions": conversation_data.get("agent_instructions", ""),
        }

    def _parse_mistakes(self, raw_mistakes: List[Dict]) -> List[Mistake]:
        """Safely parse raw mistake dicts into Mistake pydantic models."""
        mistakes: List[Mistake] = []
        for m in raw_mistakes:
            try:
                mistake = Mistake(
                    type=MistakeType(m.get("type", "other")),
                    description=m.get("description", "No description provided"),
                    timestamp_in_call=float(m.get("timestamp_in_call", 0.0)),
                    recommended_correction=m.get(
                        "recommended_correction", "No correction provided"
                    ),
                    severity=SeverityLevel(m.get("severity", "medium")),
                    confidence_score=float(m.get("confidence_score", 0.5)),
                )
                mistakes.append(mistake)
            except Exception as exc:
                logger.warning("Could not parse mistake entry: %s — %s", m, exc)
        return mistakes

    def _merge_hallucinations_as_mistakes(
        self,
        existing_mistakes: List[Mistake],
        hallucinations: List[Dict],
    ) -> List[Mistake]:
        """Convert raw hallucination results into Mistake records and merge."""
        for h in hallucinations:
            try:
                m = Mistake(
                    type=MistakeType.hallucination,
                    description=f"Hallucination detected: {h.get('issue', 'Unknown issue')}",
                    timestamp_in_call=float(h.get("timestamp_in_call", 0.0)),
                    recommended_correction=(
                        f"Remove or correct the claim: '{h.get('claim', '')}'"
                    ),
                    severity=SeverityLevel.critical,
                    confidence_score=float(h.get("confidence_score", 0.9)),
                )
                existing_mistakes.append(m)
            except Exception as exc:
                logger.warning("Could not merge hallucination: %s — %s", h, exc)
        return existing_mistakes

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def evaluate_conversation(
        self, conversation_data: Dict[str, Any]
    ) -> EvaluationReport:
        """
        Full evaluation pipeline for a completed conversation.

        Steps:
          1. Build tenant context from conversation payload.
          2. Run GPT-4o transcript analysis.
          3. Run hallucination detection against knowledge base context.
          4. Merge results into an EvaluationReport.
          5. Persist to MongoDB.
          6. Publish 'evaluation.completed' Kafka event.

        Args:
            conversation_data: Full conversation event payload from Kafka.

        Returns:
            Persisted EvaluationReport instance.
        """
        conversation_id = conversation_data.get("conversation_id", "unknown")
        tenant_id = conversation_data.get("tenant_id", "unknown")
        lead_id = conversation_data.get("lead_id", "unknown")

        logger.info(
            "Starting evaluation pipeline for conversation %s (tenant: %s)",
            conversation_id,
            tenant_id,
        )

        tenant_context = self._build_tenant_context(conversation_data)

        # Step 1: Full transcript analysis
        analysis = await self._analyzer.analyze(conversation_data, tenant_context)

        # Step 2: Hallucination detection
        transcript_text = " ".join(
            msg.get("content", "")
            for msg in conversation_data.get("messages", [])
            if msg.get("role") == "assistant"
        )
        knowledge_base_context = conversation_data.get("faq_context", "")
        hallucinations = await self._analyzer.detect_hallucinations(
            transcript_text, knowledge_base_context
        )

        # Step 3: Parse and merge mistakes
        mistakes = self._parse_mistakes(analysis.get("mistakes", []))
        mistakes = self._merge_hallucinations_as_mistakes(mistakes, hallucinations)

        # Step 4: Construct EvaluationReport
        report = EvaluationReport(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            lead_id=lead_id,
            overall_score=float(analysis.get("overall_score", 5.0)),
            qualification_accuracy=float(analysis.get("qualification_accuracy", 0.5)),
            mistakes=mistakes,
            strengths=analysis.get("strengths", []),
            missed_opportunities=analysis.get("missed_opportunities", []),
            coaching_feedback=analysis.get(
                "coaching_feedback", "Evaluation feedback unavailable."
            ),
            sentiment_trend=SentimentTrend(
                analysis.get("sentiment_trend", "stable")
            ),
            improvement_status=ImprovementStatus.pending,
            created_at=datetime.utcnow(),
        )

        # Step 5: Persist to MongoDB
        doc = report.model_dump(by_alias=True, exclude={"id"})
        result = await self._db[_COLLECTION].insert_one(doc)
        report.id = str(result.inserted_id)
        logger.info("Evaluation report persisted with id %s", report.id)

        # Step 6: Publish Kafka event
        await self._publish_evaluation_event(report)

        return report

    async def _publish_evaluation_event(self, report: EvaluationReport) -> None:
        """Publish evaluation.completed event to Kafka."""
        try:
            event = {
                "event_type": "evaluation.completed",
                "evaluation_id": report.id,
                "conversation_id": report.conversation_id,
                "tenant_id": report.tenant_id,
                "lead_id": report.lead_id,
                "overall_score": report.overall_score,
                "mistakes_count": len(report.mistakes),
                "critical_mistakes": sum(
                    1
                    for m in report.mistakes
                    if m.severity == SeverityLevel.critical
                ),
                "improvement_status": report.improvement_status.value,
                "created_at": report.created_at.isoformat(),
            }
            await self._producer.publish(
                topic=settings.kafka_topic_evaluation_completed,
                key=report.conversation_id,
                value=event,
            )
            logger.info("Published evaluation.completed event for %s", report.id)
        except Exception as exc:
            logger.error(
                "Failed to publish evaluation event for %s: %s", report.id, exc
            )

    async def get_agent_performance_summary(
        self, tenant_id: str, days: int = 30
    ) -> Dict[str, Any]:
        """
        Aggregate performance metrics across recent evaluations for a tenant.

        Args:
            tenant_id: The tenant identifier.
            days:      Look-back window in days (default 30).

        Returns:
            Dict with avg_score, total_evaluations, mistake breakdown, trends, etc.
        """
        since = datetime.utcnow() - timedelta(days=days)
        pipeline = [
            {
                "$match": {
                    "tenant_id": tenant_id,
                    "created_at": {"$gte": since},
                }
            },
            {
                "$facet": {
                    "overview": [
                        {
                            "$group": {
                                "_id": None,
                                "total_evaluations": {"$sum": 1},
                                "avg_score": {"$avg": "$overall_score"},
                                "min_score": {"$min": "$overall_score"},
                                "max_score": {"$max": "$overall_score"},
                                "avg_qualification_accuracy": {
                                    "$avg": "$qualification_accuracy"
                                },
                            }
                        }
                    ],
                    "score_trend": [
                        {
                            "$group": {
                                "_id": {
                                    "$dateToString": {
                                        "format": "%Y-%m-%d",
                                        "date": "$created_at",
                                    }
                                },
                                "avg_score": {"$avg": "$overall_score"},
                                "count": {"$sum": 1},
                            }
                        },
                        {"$sort": {"_id": 1}},
                    ],
                    "sentiment_distribution": [
                        {
                            "$group": {
                                "_id": "$sentiment_trend",
                                "count": {"$sum": 1},
                            }
                        }
                    ],
                    "improvement_status_distribution": [
                        {
                            "$group": {
                                "_id": "$improvement_status",
                                "count": {"$sum": 1},
                            }
                        }
                    ],
                }
            },
        ]

        cursor = self._db[_COLLECTION].aggregate(pipeline)
        results = await cursor.to_list(length=None)

        if not results:
            return {
                "tenant_id": tenant_id,
                "period_days": days,
                "total_evaluations": 0,
                "avg_score": None,
                "score_trend": [],
                "sentiment_distribution": {},
                "improvement_status_distribution": {},
            }

        facet = results[0]
        overview = (facet.get("overview") or [{}])[0]
        overview.pop("_id", None)

        return {
            "tenant_id": tenant_id,
            "period_days": days,
            **overview,
            "score_trend": facet.get("score_trend", []),
            "sentiment_distribution": {
                item["_id"]: item["count"]
                for item in facet.get("sentiment_distribution", [])
                if item.get("_id")
            },
            "improvement_status_distribution": {
                item["_id"]: item["count"]
                for item in facet.get("improvement_status_distribution", [])
                if item.get("_id")
            },
        }
