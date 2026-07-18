"""
Kafka producer — publishes conversation lifecycle events.

Topics published:
  - conversation.completed   Full conversation payload when a conversation ends.
  - call.initiated           Emitted immediately when an outbound call is triggered.
  - meeting.booked           Emitted when the agent successfully books a meeting.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)


def _default_serialiser(obj: Any) -> str:
    """JSON serialiser that handles datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")


class EventProducer:
    """
    Thin wrapper around AIOKafkaProducer that serialises events as JSON and
    provides typed helper methods for each published topic.

    Example::

        producer = EventProducer(bootstrap_servers="kafka:9092")
        await producer.start()
        await producer.publish_conversation_completed(conversation_data)
        await producer.stop()
    """

    def __init__(self, bootstrap_servers: str) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._producer: AIOKafkaProducer | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Create and start the underlying Kafka producer."""
        logger.info("Starting Kafka producer (brokers=%s)…", self._bootstrap_servers)
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, default=_default_serialiser).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            # Reliability settings
            acks="all",
            enable_idempotence=True,
            max_batch_size=16384,
            linger_ms=5,
            compression_type="gzip",
        )
        await self._producer.start()
        logger.info("Kafka producer started.")

    async def stop(self) -> None:
        """Flush pending messages and close the producer."""
        if self._producer:
            await self._producer.stop()
            logger.info("Kafka producer stopped.")

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _publish(
        self,
        topic: str,
        payload: dict[str, Any],
        key: str | None = None,
    ) -> None:
        """
        Publish a single JSON message to *topic*.

        Args:
            topic:   Kafka topic name.
            payload: Python dict — will be JSON-encoded.
            key:     Optional Kafka message key (used for partitioning).
        """
        if self._producer is None:
            raise RuntimeError("EventProducer.start() must be called before publishing.")

        # Enrich with standard envelope fields
        envelope: dict[str, Any] = {
            "event_timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "agent-a-service",
            **payload,
        }

        try:
            await self._producer.send_and_wait(topic, value=envelope, key=key)
            logger.debug("Published event | topic=%s key=%s", topic, key)
        except Exception as exc:
            logger.error(
                "Failed to publish event | topic=%s key=%s error=%s",
                topic,
                key,
                exc,
                exc_info=True,
            )
            raise

    # ------------------------------------------------------------------ #
    # Typed publish helpers
    # ------------------------------------------------------------------ #

    async def publish_conversation_completed(
        self,
        conversation_data: dict[str, Any],
    ) -> None:
        """
        Publish a ``conversation.completed`` event.

        Args:
            conversation_data: Full serialised Conversation document.
                               Must include at minimum: id, tenant_id, lead_id, status.
        """
        payload = {
            "event_type": "conversation.completed",
            "conversation_id": conversation_data.get("id"),
            "tenant_id": conversation_data.get("tenant_id"),
            "lead_id": conversation_data.get("lead_id"),
            "channel": conversation_data.get("channel"),
            "status": conversation_data.get("status"),
            "meeting_booked": conversation_data.get("meeting_booked", False),
            "qualification_result": conversation_data.get("qualification_result"),
            "summary": conversation_data.get("summary"),
            "metrics": conversation_data.get("metrics"),
            "ended_at": conversation_data.get("ended_at"),
        }
        await self._publish(
            topic="conversation.completed",
            payload=payload,
            key=conversation_data.get("conversation_id") or conversation_data.get("id"),
        )

    async def publish_call_initiated(
        self,
        conversation_id: str,
        tenant_id: str,
        lead_id: str,
        phone_number: str,
        twilio_call_sid: str | None = None,
    ) -> None:
        """
        Publish a ``call.initiated`` event immediately when an outbound call starts.

        Args:
            conversation_id: The conversation document ID.
            tenant_id:       Tenant identifier.
            lead_id:         Lead identifier.
            phone_number:    Destination phone number (E.164 format).
            twilio_call_sid: Twilio Call SID, if available at publish time.
        """
        payload = {
            "event_type": "call.initiated",
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "phone_number": phone_number,
            "twilio_call_sid": twilio_call_sid,
        }
        await self._publish(
            topic="call.initiated",
            payload=payload,
            key=conversation_id,
        )

    async def publish_meeting_booked(
        self,
        conversation_id: str,
        tenant_id: str,
        lead_id: str,
        meeting_details: dict[str, Any],
    ) -> None:
        """
        Publish a ``meeting.booked`` event when the agent successfully books a meeting.

        Args:
            conversation_id: The conversation document ID.
            tenant_id:       Tenant identifier.
            lead_id:         Lead identifier.
            meeting_details: Dict with proposed_date, proposed_time, meeting_type, etc.
        """
        payload = {
            "event_type": "meeting.booked",
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "meeting_details": meeting_details,
        }
        await self._publish(
            topic="meeting.booked",
            payload=payload,
            key=conversation_id,
        )
