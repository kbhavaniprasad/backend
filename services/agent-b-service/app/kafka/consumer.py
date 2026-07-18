"""
Kafka consumer for agent-b-service.
Consumes 'conversation.completed' events and drives the full
evaluation → learning generation → auto-apply pipeline.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaConsumer

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EvaluationConsumer:
    """
    Async Kafka consumer that listens on the 'conversation.completed' topic
    and orchestrates:
      1. PerformanceEvaluator.evaluate_conversation()
      2. LearningGenerator.generate_learnings()
      3. Auto-application of high-confidence learnings
      4. KnowledgeUpdater.update_from_successful_conversation()

    The consumer is injected with its dependencies at construction time so
    they can be tested in isolation.
    """

    def __init__(
        self,
        performance_evaluator,
        learning_generator,
        knowledge_updater,
    ) -> None:
        self._evaluator = performance_evaluator
        self._learning_gen = learning_generator
        self._knowledge_updater = knowledge_updater
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Kafka consumer loop in the background."""
        self._consumer = AIOKafkaConsumer(
            settings.kafka_topic_conversation_completed,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,          # Manual commit for reliability
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        await self._consumer.start()
        self._running = True
        logger.info(
            "Kafka consumer started — topic: %s, group: %s",
            settings.kafka_topic_conversation_completed,
            settings.kafka_consumer_group_id,
        )
        await self._consume_loop()

    async def stop(self) -> None:
        """Gracefully stop the consumer."""
        self._running = False
        if self._consumer:
            await self._consumer.stop()
            logger.info("Kafka consumer stopped")

    # ------------------------------------------------------------------
    # Main consume loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """
        Core message processing loop.
        Each message is processed fully before committing the offset,
        ensuring at-least-once delivery semantics.
        """
        assert self._consumer is not None

        while self._running:
            try:
                async for message in self._consumer:
                    if not self._running:
                        break

                    conversation_data: Dict[str, Any] = message.value
                    conversation_id = conversation_data.get(
                        "conversation_id", "unknown"
                    )

                    logger.info(
                        "Received conversation.completed event for %s "
                        "(partition=%d, offset=%d)",
                        conversation_id,
                        message.partition,
                        message.offset,
                    )

                    await self._process_event(conversation_data)

                    # Commit offset only after successful processing
                    await self._consumer.commit()
                    logger.debug(
                        "Committed offset %d for partition %d",
                        message.offset,
                        message.partition,
                    )

            except Exception as exc:
                logger.error(
                    "Error in Kafka consume loop: %s — will restart loop", exc,
                    exc_info=True,
                )
                # Brief pause before retrying to avoid tight error loops
                import asyncio
                await asyncio.sleep(2)

    # ------------------------------------------------------------------
    # Event processing pipeline
    # ------------------------------------------------------------------

    async def _process_event(self, conversation_data: Dict[str, Any]) -> None:
        """
        Full evaluation pipeline for a single conversation.completed event:
          1. Evaluate the conversation.
          2. Generate learnings from critical/high mistakes.
          3. Auto-apply high-confidence learnings.
          4. Update knowledge base from successful conversations.
        """
        conversation_id = conversation_data.get("conversation_id", "unknown")
        tenant_id = conversation_data.get("tenant_id", "unknown")

        # Step 1: Evaluate
        evaluation = None
        try:
            evaluation = await self._evaluator.evaluate_conversation(conversation_data)
            logger.info(
                "Evaluation complete for %s — score: %.1f",
                conversation_id,
                evaluation.overall_score,
            )
        except Exception as exc:
            logger.error(
                "Evaluation failed for conversation %s: %s",
                conversation_id,
                exc,
                exc_info=True,
            )
            return

        # Step 2: Generate learnings
        learnings = []
        try:
            learnings = await self._learning_gen.generate_learnings(evaluation)
            logger.info(
                "Generated %d learnings for conversation %s",
                len(learnings),
                conversation_id,
            )
        except Exception as exc:
            logger.error(
                "Learning generation failed for evaluation %s: %s",
                evaluation.id,
                exc,
                exc_info=True,
            )

        # Step 3: Auto-apply high-confidence learnings
        for learning in learnings:
            if learning.confidence_score >= settings.high_confidence_threshold:
                try:
                    success = await self._learning_gen.apply_learning(
                        learning,
                        agent_a_service_url=settings.agent_a_service_url,
                    )
                    if success:
                        logger.info(
                            "Auto-applied learning %s (confidence=%.2f)",
                            learning.id,
                            learning.confidence_score,
                        )
                    else:
                        logger.warning(
                            "Auto-apply failed for learning %s", learning.id
                        )
                except Exception as exc:
                    logger.error(
                        "Error auto-applying learning %s: %s",
                        learning.id,
                        exc,
                        exc_info=True,
                    )

        # Step 4: Update knowledge base from successful conversations
        try:
            await self._knowledge_updater.update_from_successful_conversation(
                conversation={
                    **conversation_data,
                    "qualification_accuracy": evaluation.qualification_accuracy,
                },
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.error(
                "Knowledge update failed for conversation %s: %s",
                conversation_id,
                exc,
                exc_info=True,
            )

        logger.info(
            "Pipeline complete for conversation %s (tenant: %s)",
            conversation_id,
            tenant_id,
        )
