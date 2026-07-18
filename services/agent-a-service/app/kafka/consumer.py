"""
LeadEventConsumer — Kafka consumer for the 'lead.created' topic.

Responsibilities:
- Consume 'lead.created' events.
- Determine the appropriate engagement channel (voice / WhatsApp / SMS / Instagram / chat).
- Trigger the outbound call via the Voice Service (HTTP) or send an initial message via
  the Messaging Service, depending on the lead's available contact info and source.
- Track in-progress leads in Redis to prevent duplicate processing.
- Publish a 'call.initiated' event via EventProducer when a call is triggered.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer

from app.config import settings
from app.kafka.producer import EventProducer

logger = logging.getLogger(__name__)

# Redis key prefix / TTL for deduplication
_REDIS_PREFIX = "agent:lead_processing:"
_REDIS_TTL_SECONDS = 3600  # 1 hour

# How long to wait for the voice / messaging service (seconds)
_HTTP_TIMEOUT = 15.0


class LeadEventConsumer:
    """
    Kafka consumer that reacts to 'lead.created' events and triggers the agent.

    Example::

        consumer = LeadEventConsumer(producer=event_producer, redis_client=redis_client)
        await consumer.start()           # runs forever in background
        ...
        await consumer.stop()
    """

    def __init__(
        self,
        producer: EventProducer,
        redis_client: aioredis.Redis,  # type: ignore[type-arg]
    ) -> None:
        self._producer = producer
        self._redis = redis_client
        self._consumer: AIOKafkaConsumer | None = None
        self._running = False
        self._http_client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """
        Initialise the Kafka consumer and start the processing loop.
        This method runs indefinitely; run it as a background asyncio task.
        """
        logger.info(
            "Starting LeadEventConsumer (brokers=%s group=%s topic=%s)…",
            settings.kafka_bootstrap_servers,
            settings.kafka_consumer_group_id,
            settings.kafka_topic_lead_created,
        )

        self._http_client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)

        self._consumer = AIOKafkaConsumer(
            settings.kafka_topic_lead_created,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            auto_commit_interval_ms=1000,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            # Consumer resilience settings
            session_timeout_ms=30_000,
            heartbeat_interval_ms=10_000,
            max_poll_records=10,
        )

        await self._consumer.start()
        self._running = True
        logger.info("LeadEventConsumer started — listening for events…")

        try:
            await self._consume_loop()
        except asyncio.CancelledError:
            logger.info("LeadEventConsumer task cancelled.")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Gracefully shut down the consumer."""
        self._running = False
        if self._consumer:
            await self._consumer.stop()
            logger.info("LeadEventConsumer stopped.")
        if self._http_client:
            await self._http_client.aclose()

    # ------------------------------------------------------------------ #
    # Consume loop
    # ------------------------------------------------------------------ #

    async def _consume_loop(self) -> None:
        """Main poll loop — processes records as they arrive."""
        assert self._consumer is not None

        async for message in self._consumer:
            if not self._running:
                break

            try:
                event: dict[str, Any] = message.value  # already deserialised
                logger.info(
                    "Received event | topic=%s partition=%d offset=%d",
                    message.topic,
                    message.partition,
                    message.offset,
                )
                await self._process_lead_created_event(event)
            except Exception as exc:
                logger.error(
                    "Unhandled error processing Kafka message | offset=%d error=%s",
                    message.offset,
                    exc,
                    exc_info=True,
                )
                # Continue consuming — we do not crash on individual message failures

    # ------------------------------------------------------------------ #
    # Event processing
    # ------------------------------------------------------------------ #

    async def _process_lead_created_event(self, event: dict[str, Any]) -> None:
        """
        Handle a single 'lead.created' event.

        Deduplication logic:
        - Stores a Redis key ``agent:lead_processing:<lead_id>`` with a 1-hour TTL.
        - If the key already exists, the event is skipped (duplicate).

        Engagement routing:
        - Lead has phone number  → trigger outbound call via Voice Service.
        - Lead source is whatsapp / instagram → send initial message via Messaging Service.
        - Otherwise → send SMS / chat message via Messaging Service.
        """
        lead_id: str = event.get("lead_id") or event.get("id", "unknown")
        tenant_id: str = event.get("tenant_id", "unknown")

        # ── Deduplication ────────────────────────────────────────────────
        redis_key = f"{_REDIS_PREFIX}{tenant_id}:{lead_id}"
        already_processing = await self._redis.get(redis_key)
        if already_processing:
            logger.info(
                "Skipping duplicate lead event | tenant=%s lead=%s",
                tenant_id,
                lead_id,
            )
            return

        # Claim the lead — set with TTL to auto-expire if processing fails
        await self._redis.setex(redis_key, _REDIS_TTL_SECONDS, "processing")
        logger.info("Processing lead | tenant=%s lead=%s", tenant_id, lead_id)

        try:
            phone_number: str | None = event.get("phone")
            source: str = (event.get("source") or "").lower()

            # ── Routing decision ─────────────────────────────────────────
            if phone_number:
                await self._trigger_outbound_call(
                    lead_id=lead_id,
                    tenant_id=tenant_id,
                    phone_number=phone_number,
                    lead_data=event,
                )
            elif source in {"whatsapp", "instagram"}:
                await self._send_initial_message(
                    lead_id=lead_id,
                    tenant_id=tenant_id,
                    channel=source,
                    lead_data=event,
                )
            else:
                # Default to SMS or chat channel
                channel = source if source in {"sms", "chat"} else "chat"
                await self._send_initial_message(
                    lead_id=lead_id,
                    tenant_id=tenant_id,
                    channel=channel,
                    lead_data=event,
                )

            # Mark as done
            await self._redis.setex(redis_key, _REDIS_TTL_SECONDS, "completed")
            logger.info("Lead processing complete | tenant=%s lead=%s", tenant_id, lead_id)

        except Exception as exc:
            logger.error(
                "Failed to process lead | tenant=%s lead=%s error=%s",
                tenant_id,
                lead_id,
                exc,
                exc_info=True,
            )
            # Remove the Redis lock so the event can be retried
            await self._redis.delete(redis_key)

    # ------------------------------------------------------------------ #
    # Downstream service calls
    # ------------------------------------------------------------------ #

    async def _trigger_outbound_call(
        self,
        lead_id: str,
        tenant_id: str,
        phone_number: str,
        lead_data: dict[str, Any],
    ) -> None:
        """
        Request the Voice Service to initiate an outbound call to the lead.

        Publishes a 'call.initiated' Kafka event after successfully requesting the call.
        """
        assert self._http_client is not None

        url = f"{settings.voice_service_url}/api/v1/calls/outbound"
        payload = {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "to_number": phone_number,
            "from_number": settings.twilio_phone_number,
            "lead_data": lead_data,
        }

        logger.info(
            "Triggering outbound call | tenant=%s lead=%s phone=%s",
            tenant_id,
            lead_id,
            phone_number,
        )

        response = await self._http_client.post(url, json=payload)
        response.raise_for_status()

        response_data: dict[str, Any] = response.json()
        conversation_id: str = response_data.get("conversation_id", lead_id)
        twilio_call_sid: str | None = response_data.get("call_sid")

        # Publish call.initiated event
        await self._producer.publish_call_initiated(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            lead_id=lead_id,
            phone_number=phone_number,
            twilio_call_sid=twilio_call_sid,
        )
        logger.info(
            "Outbound call triggered | conversation=%s sid=%s",
            conversation_id,
            twilio_call_sid,
        )

    async def _send_initial_message(
        self,
        lead_id: str,
        tenant_id: str,
        channel: str,
        lead_data: dict[str, Any],
    ) -> None:
        """
        Request the Messaging Service to send the agent's opening message via
        WhatsApp, Instagram, SMS, or chat.
        """
        assert self._http_client is not None

        url = f"{settings.lead_service_url}/api/v1/messaging/send-initial"
        payload = {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "channel": channel,
            "lead_data": lead_data,
        }

        logger.info(
            "Sending initial message | tenant=%s lead=%s channel=%s",
            tenant_id,
            lead_id,
            channel,
        )

        response = await self._http_client.post(url, json=payload)
        response.raise_for_status()
        logger.info(
            "Initial message sent | tenant=%s lead=%s channel=%s",
            tenant_id,
            lead_id,
            channel,
        )
