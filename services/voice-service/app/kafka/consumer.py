"""
Kafka Consumer — Voice Service
Listens to 'lead.created' events and initiates Retell AI calls.
"""

import asyncio
import json
import logging
from datetime import datetime

import aioredis
from aiokafka import AIOKafkaConsumer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import Settings
from app.kafka.producer import KafkaProducerService
from app.retell.client import RetellClient
from app.retell.orchestrator import CallOrchestrator

logger = logging.getLogger(__name__)

# Lead sources that support voice calls
VOICE_CALLABLE_SOURCES = {
    "facebook_ads", "google_ads", "linkedin_ads",
    "website_form", "crm", "manual",
}

# Lead sources that use messaging instead (no phone call)
MESSAGING_SOURCES = {"whatsapp", "instagram_dm"}


class LeadEventConsumer:
    """
    Kafka consumer for 'lead.created' events.
    Triggers instant Retell AI outbound calls when a new lead arrives.

    Target: Contact lead within 60 seconds of creation.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        retell_client: RetellClient,
        redis: aioredis.Redis,
        db: AsyncIOMotorDatabase,
        kafka_producer: KafkaProducerService,
        settings: Settings,
    ):
        self.bootstrap_servers = bootstrap_servers
        self.retell_client = retell_client
        self.redis = redis
        self.db = db
        self.kafka_producer = kafka_producer
        self.settings = settings
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start consuming lead.created events."""
        self._consumer = AIOKafkaConsumer(
            "lead.created",
            bootstrap_servers=self.bootstrap_servers,
            group_id="voice-service-lead-consumer",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("Lead event consumer started — listening for new leads")

    async def stop(self):
        """Stop the consumer gracefully."""
        if self._task:
            self._task.cancel()
        if self._consumer:
            await self._consumer.stop()

    async def _consume_loop(self):
        """Main consumption loop."""
        async for message in self._consumer:
            try:
                await self._process_lead_created(message.value)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error processing lead.created event: %s", exc)

    async def _process_lead_created(self, event: dict):
        """
        Process a lead.created event and trigger appropriate action.

        For voice-callable leads (phone number available):
          → Initiate Retell AI outbound call immediately

        For messaging leads (WhatsApp/Instagram):
          → Publish to messaging topic for the messaging service

        Call timing target: < 60 seconds from lead creation.
        """
        lead_id   = event.get("lead_id")
        tenant_id = event.get("tenant_id")
        source    = event.get("source", "")
        phone     = event.get("phone")
        first_name = event.get("first_name", "")
        last_name  = event.get("last_name", "")
        company    = event.get("company", "")

        lead_created_at = event.get("timestamp")
        logger.info(
            "Processing lead.created",
            extra={"lead_id": lead_id, "source": source, "has_phone": bool(phone)},
        )

        if source in MESSAGING_SOURCES or not phone:
            # Route to messaging pipeline
            await self.kafka_producer.publish(
                topic="lead.messaging_contact",
                key=lead_id,
                value={
                    "event": "lead.messaging_contact",
                    "lead_id": lead_id,
                    "tenant_id": tenant_id,
                    "source": source,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
            return

        if source not in VOICE_CALLABLE_SOURCES and source not in MESSAGING_SOURCES:
            logger.info("Unknown source, defaulting to voice if phone available")

        if not phone:
            logger.info("No phone number for lead — skipping call", extra={"lead_id": lead_id})
            return

        # Publish call.initiated event BEFORE making the call (for dashboards)
        await self.kafka_producer.publish(
            topic="call.initiated",
            key=lead_id,
            value={
                "event": "call.initiated",
                "lead_id": lead_id,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        # Build orchestrator and initiate Retell call
        orchestrator = CallOrchestrator(
            retell_client=self.retell_client,
            redis=self.redis,
            db=self.db,
            settings=self.settings,
        )

        lead_data = {
            "first_name": first_name,
            "last_name": last_name,
            "company": company,
            "source": source,
            "job_title": event.get("job_title", ""),
        }

        result = await orchestrator.initiate_call(
            lead_id=lead_id,
            tenant_id=tenant_id,
            phone_number=phone,
            lead_data=lead_data,
        )

        if result:
            logger.info(
                "✅ Retell call initiated for new lead",
                extra={
                    "call_id": result.get("call_id"),
                    "lead_id": lead_id,
                    "elapsed_from_creation": lead_created_at,
                },
            )
        else:
            logger.warning("Call not initiated for lead", extra={"lead_id": lead_id})
