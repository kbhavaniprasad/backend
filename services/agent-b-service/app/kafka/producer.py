"""
Kafka producer for agent-b-service.
Publishes events to: evaluation.completed, learning.applied, report.generated.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaProducer

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _json_serializer(value: Any) -> bytes:
    """Serialise a Python object to UTF-8 encoded JSON bytes."""
    return json.dumps(value, default=str).encode("utf-8")


def _key_serializer(key: Optional[str]) -> Optional[bytes]:
    """Serialise a string key to bytes, or return None."""
    return key.encode("utf-8") if key else None


class EvaluationProducer:
    """
    Async Kafka producer wrapper for Agent B events.

    Topics published:
      - evaluation.completed  — full evaluation report available
      - learning.applied      — a learning has been applied to Agent A
      - report.generated      — a business/performance report has been generated
    """

    def __init__(self) -> None:
        self._producer: Optional[AIOKafkaProducer] = None
        self._bootstrap_servers = settings.kafka_bootstrap_servers

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialise and start the underlying AIOKafkaProducer."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=_json_serializer,
            key_serializer=_key_serializer,
            # Reliability settings
            acks="all",
            enable_idempotence=True,
            retries=5,
            max_in_flight_requests_per_connection=1,
        )
        await self._producer.start()
        logger.info(
            "Kafka producer started — broker: %s", self._bootstrap_servers
        )

    async def stop(self) -> None:
        """Flush and stop the producer gracefully."""
        if self._producer:
            await self._producer.stop()
            logger.info("Kafka producer stopped")

    # ------------------------------------------------------------------
    # Core publish helper
    # ------------------------------------------------------------------

    async def publish(
        self,
        topic: str,
        value: Dict[str, Any],
        key: Optional[str] = None,
    ) -> None:
        """
        Publish a message to the specified Kafka topic.

        Args:
            topic: Target Kafka topic name.
            value: Message payload as a dict (will be JSON-serialised).
            key:   Optional partition key.
        """
        if not self._producer:
            logger.error("Producer not started — cannot publish to %s", topic)
            return

        value.setdefault("published_at", datetime.utcnow().isoformat())
        value.setdefault("service", settings.service_name)

        try:
            await self._producer.send_and_wait(topic=topic, value=value, key=key)
            logger.debug(
                "Published to [%s] key=%s payload_keys=%s",
                topic,
                key,
                list(value.keys()),
            )
        except Exception as exc:
            logger.error(
                "Failed to publish to [%s] key=%s: %s", topic, key, exc
            )
            raise

    # ------------------------------------------------------------------
    # Convenience methods per topic
    # ------------------------------------------------------------------

    async def publish_evaluation_completed(
        self,
        evaluation_id: str,
        conversation_id: str,
        tenant_id: str,
        lead_id: str,
        overall_score: float,
        mistakes_count: int,
        improvement_status: str,
    ) -> None:
        """Publish an evaluation.completed event."""
        await self.publish(
            topic=settings.kafka_topic_evaluation_completed,
            key=conversation_id,
            value={
                "event_type": "evaluation.completed",
                "evaluation_id": evaluation_id,
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "overall_score": overall_score,
                "mistakes_count": mistakes_count,
                "improvement_status": improvement_status,
            },
        )

    async def publish_learning_applied(
        self,
        learning_id: str,
        tenant_id: str,
        category: str,
        severity: str,
        source_evaluation_id: str,
    ) -> None:
        """Publish a learning.applied event."""
        await self.publish(
            topic=settings.kafka_topic_learning_applied,
            key=learning_id,
            value={
                "event_type": "learning.applied",
                "learning_id": learning_id,
                "tenant_id": tenant_id,
                "category": category,
                "severity": severity,
                "source_evaluation_id": source_evaluation_id,
                "applied_at": datetime.utcnow().isoformat(),
            },
        )

    async def publish_report_generated(
        self,
        report_id: str,
        tenant_id: str,
        report_type: str,
        period_start: str,
        period_end: str,
    ) -> None:
        """Publish a report.generated event."""
        await self.publish(
            topic=settings.kafka_topic_report_generated,
            key=report_id,
            value={
                "event_type": "report.generated",
                "report_id": report_id,
                "tenant_id": tenant_id,
                "report_type": report_type,
                "period_start": period_start,
                "period_end": period_end,
                "generated_at": datetime.utcnow().isoformat(),
            },
        )
