"""
app/kafka/producer.py – Async Kafka producer service for lead-service.

Wraps AIOKafkaProducer to provide typed helpers for publishing domain events.
All messages are JSON-encoded with a consistent envelope structure.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _serialize(value: Any) -> bytes:
    """JSON-serialize a Python object to UTF-8 bytes."""
    return json.dumps(value, default=str).encode("utf-8")


def _key(tenant_id: str | uuid.UUID) -> bytes:
    """Use tenant_id as the Kafka partition key for locality."""
    return str(tenant_id).encode("utf-8")


class KafkaProducerService:
    """
    Async Kafka producer encapsulating all lead-domain event publishing.

    Lifecycle
    ---------
    Call ``await start()`` on application startup and ``await stop()`` on
    shutdown.  Inject the singleton via FastAPI's ``app.state``.

    Example
    -------
    >>> producer = KafkaProducerService(bootstrap_servers="localhost:9092")
    >>> await producer.start()
    >>> await producer.publish_lead_created(lead_id=..., tenant_id=..., ...)
    >>> await producer.stop()
    """

    def __init__(self, bootstrap_servers: str | None = None) -> None:
        self._bootstrap_servers: str = (
            bootstrap_servers or settings.kafka_bootstrap_servers
        )
        self._producer: AIOKafkaProducer | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Create and start the underlying AIOKafkaProducer."""
        if self._producer is not None:
            logger.warning("KafkaProducerService.start() called while already running.")
            return

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            client_id=settings.kafka_client_id,
            value_serializer=_serialize,
            key_serializer=lambda k: k if isinstance(k, bytes) else str(k).encode(),
            # Reliability settings
            acks="all",                   # wait for all ISR acknowledgements
            enable_idempotence=True,      # exactly-once producer semantics
            max_batch_size=16_384,        # 16 KB batches
            linger_ms=5,                  # micro-batching window
            compression_type="gzip",
            request_timeout_ms=30_000,
            retry_backoff_ms=200,
        )
        await self._producer.start()
        logger.info(
            "Kafka producer started. bootstrap_servers=%s",
            self._bootstrap_servers,
        )

    async def stop(self) -> None:
        """Flush pending messages and stop the producer gracefully."""
        if self._producer is None:
            return
        try:
            await self._producer.stop()
            logger.info("Kafka producer stopped.")
        except Exception as exc:  # noqa: BLE001
            logger.error("Error stopping Kafka producer: %s", exc)
        finally:
            self._producer = None

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _send(
        self,
        topic: str,
        payload: dict[str, Any],
        key: bytes | None = None,
    ) -> None:
        """
        Low-level send with error handling.

        Retries are handled by AIOKafkaProducer's built-in retry mechanism.
        We log failures rather than re-raising so that Kafka issues never
        block the HTTP response.
        """
        if self._producer is None:
            logger.error(
                "Kafka producer is not initialised. Dropping message to topic=%s",
                topic,
            )
            return

        try:
            record_metadata = await self._producer.send_and_wait(
                topic, value=payload, key=key
            )
            logger.debug(
                "Kafka message sent. topic=%s partition=%s offset=%s",
                record_metadata.topic,
                record_metadata.partition,
                record_metadata.offset,
            )
        except KafkaError as exc:
            logger.error(
                "Failed to publish Kafka message. topic=%s error=%s",
                topic,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Unexpected error publishing Kafka message. topic=%s error=%s",
                topic,
                exc,
            )

    # ── Domain event publishers ────────────────────────────────────────────────

    async def publish_lead_created(
        self,
        lead_id: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
        lead_data: dict[str, Any],
    ) -> None:
        """
        Publish a ``lead.created`` event.

        Parameters
        ----------
        lead_id:
            UUID of the newly created lead.
        tenant_id:
            UUID of the owning tenant (used as Kafka partition key).
        lead_data:
            Dict containing at minimum: source, phone, email,
            first_name, last_name.  Additional fields are forwarded as-is.
        """
        payload: dict[str, Any] = {
            "event": "lead.created",
            "event_id": str(uuid.uuid4()),
            "lead_id": str(lead_id),
            "tenant_id": str(tenant_id),
            "source": lead_data.get("source"),
            "phone": lead_data.get("phone"),
            "email": lead_data.get("email"),
            "first_name": lead_data.get("first_name"),
            "last_name": lead_data.get("last_name"),
            "assigned_agent_id": str(lead_data.get("assigned_agent_id") or ""),
            "timestamp": _utc_now_iso(),
            # Forward attribution metadata when available
            "ad_campaign_id": lead_data.get("ad_campaign_id"),
            "utm_source": lead_data.get("utm_source"),
            "utm_medium": lead_data.get("utm_medium"),
            "utm_campaign": lead_data.get("utm_campaign"),
        }
        await self._send(
            topic=settings.kafka_topic_lead_created,
            payload=payload,
            key=_key(tenant_id),
        )
        logger.info(
            "Published lead.created event. lead_id=%s tenant_id=%s",
            lead_id,
            tenant_id,
        )

    async def publish_lead_status_changed(
        self,
        lead_id: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
        old_status: str,
        new_status: str,
        changed_by: uuid.UUID | str | None = None,
        reason: str | None = None,
    ) -> None:
        """
        Publish a ``lead.status_changed`` event.

        Parameters
        ----------
        lead_id:
            UUID of the lead whose status changed.
        tenant_id:
            UUID of the owning tenant (used as Kafka partition key).
        old_status:
            Previous LeadStatus value.
        new_status:
            New LeadStatus value.
        changed_by:
            UUID of the user or service that triggered the change.
        reason:
            Optional human-readable reason for the change.
        """
        payload: dict[str, Any] = {
            "event": "lead.status_changed",
            "event_id": str(uuid.uuid4()),
            "lead_id": str(lead_id),
            "tenant_id": str(tenant_id),
            "old_status": old_status,
            "new_status": new_status,
            "changed_by": str(changed_by) if changed_by else None,
            "reason": reason,
            "timestamp": _utc_now_iso(),
        }
        await self._send(
            topic=settings.kafka_topic_lead_status_changed,
            payload=payload,
            key=_key(tenant_id),
        )
        logger.info(
            "Published lead.status_changed event. lead_id=%s %s → %s",
            lead_id,
            old_status,
            new_status,
        )

    async def publish_lead_assigned(
        self,
        lead_id: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
        agent_id: uuid.UUID | str,
    ) -> None:
        """
        Publish a ``lead.assigned`` event when a lead is assigned to an agent.
        """
        payload: dict[str, Any] = {
            "event": "lead.assigned",
            "event_id": str(uuid.uuid4()),
            "lead_id": str(lead_id),
            "tenant_id": str(tenant_id),
            "agent_id": str(agent_id),
            "timestamp": _utc_now_iso(),
        }
        await self._send(
            topic="lead.assigned",
            payload=payload,
            key=_key(tenant_id),
        )
        logger.info(
            "Published lead.assigned event. lead_id=%s agent_id=%s",
            lead_id,
            agent_id,
        )
