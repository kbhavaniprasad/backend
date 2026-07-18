"""Kafka Producer — Voice Service"""
import json
import logging
from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)


class KafkaProducerService:
    def __init__(self, bootstrap_servers: str):
        self.bootstrap_servers = bootstrap_servers
        self._producer: AIOKafkaProducer | None = None

    async def start(self):
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        await self._producer.start()
        logger.info("Kafka producer started")

    async def stop(self):
        if self._producer:
            await self._producer.stop()

    async def publish(self, topic: str, value: dict, key: str | None = None):
        if not self._producer:
            raise RuntimeError("Kafka producer not started")
        await self._producer.send_and_wait(topic=topic, value=value, key=key)
        logger.debug("Published to %s: %s", topic, key)
