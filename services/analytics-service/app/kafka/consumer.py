import asyncio
import json
import logging
from datetime import datetime
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
import aioredis

logger = logging.getLogger(__name__)

TOPICS = [
    "lead.created", "call.initiated", "call.started", "call.ended",
    "conversation.completed", "evaluation.completed", "learning.applied",
    "meeting.booked"
]


class AnalyticsConsumer:
    """
    Kafka consumer that listens to all platform events, calculates stats,
    caches counters in Redis, and publishes dashboard update events.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        redis_url: str,
    ):
        self.bootstrap_servers = bootstrap_servers
        self.redis_url = redis_url
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._redis: aioredis.Redis | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
        
        self._consumer = AIOKafkaConsumer(
            *TOPICS,
            bootstrap_servers=self.bootstrap_servers,
            group_id="analytics-service-group",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8")
        )
        
        await self._consumer.start()
        await self._producer.start()
        
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("Analytics event consumer & producer started")

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self._consumer:
            await self._consumer.stop()
        if self._producer:
            await self._producer.stop()
        if self._redis:
            await self._redis.close()

    async def _consume_loop(self):
        async for message in self._consumer:
            try:
                event_data = message.value
                topic = message.topic
                await self._process_event(topic, event_data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in analytics event processing: %s", e)

    async def _process_event(self, topic: str, event: dict):
        tenant_id = event.get("tenant_id")
        if not tenant_id:
            return

        # ── Increment Redis Counters ────────────────────────
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        
        # 1. Total events per tenant daily counter
        await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "total_events", 1)

        update_type = ""
        data = {}

        if topic == "lead.created":
            await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "leads_created", 1)
            update_type = "new_lead"
            data = {"lead_id": event.get("lead_id"), "source": event.get("source")}

        elif topic == "call.started":
            await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "calls_started", 1)
            update_type = "call_started"
            data = {"call_id": event.get("call_id"), "lead_id": event.get("lead_id")}

        elif topic == "call.ended":
            await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "calls_ended", 1)
            status = event.get("status")
            if status == "no_answer":
                await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "calls_no_answer", 1)
            update_type = "call_ended"
            data = {
                "call_id": event.get("call_id"),
                "status": status,
                "duration": event.get("duration_seconds")
            }

        elif topic == "conversation.completed":
            await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "conversations_completed", 1)
            # Accumulate call duration
            duration = event.get("duration_seconds", 0.0)
            await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "total_duration_sec", int(duration))
            update_type = "conversation_completed"
            data = {
                "call_id": event.get("call_id"),
                "successful": event.get("call_successful"),
                "sentiment": event.get("user_sentiment")
            }

        elif topic == "evaluation.completed":
            await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "evaluations_completed", 1)
            update_type = "evaluation_completed"
            data = {
                "evaluation_id": event.get("evaluation_id"),
                "call_id": event.get("call_id"),
                "overall_score": event.get("overall_score"),
                "mistake_count": event.get("mistake_count")
            }

        elif topic == "learning.applied":
            await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "learnings_applied", 1)
            update_type = "learning_applied"
            data = {"learning_id": event.get("learning_id"), "category": event.get("category")}

        elif topic == "meeting.booked":
            await self._redis.hincrby(f"analytics:tenant:{tenant_id}:{date_str}", "meetings_booked", 1)
            update_type = "meeting_booked"
            data = {"meeting_id": event.get("meeting_id"), "starts_at": event.get("starts_at")}

        # ── Publish WebSocket Update to Kafka topic 'dashboard.update' ──
        if update_type:
            dashboard_event = {
                "event": "dashboard.update",
                "timestamp": datetime.utcnow().isoformat(),
                "tenant_id": tenant_id,
                "update_type": update_type,
                "data": data
            }
            await self._producer.send_and_wait("dashboard.update", value=dashboard_event, key=tenant_id)
            logger.info("Published dashboard update event for tenant %s, type %s", tenant_id, update_type)
