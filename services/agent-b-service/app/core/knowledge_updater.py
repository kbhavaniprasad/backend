"""
KnowledgeUpdater — keeps the Qdrant vector knowledge base current by
ingesting successful conversation examples and correcting erroneous FAQ
embeddings discovered during evaluations.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    PointStruct,
    VectorParams,
)
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.models.evaluation import Mistake

logger = logging.getLogger(__name__)
settings = get_settings()

_LOG_COLLECTION = "embedding_update_log"

# How many tokens of context to keep per extracted Q&A pair
_MAX_PAIR_LENGTH = 600


class KnowledgeUpdater:
    """
    Manages updates to the Qdrant vector knowledge base:
      - Adds successful conversation Q&A pairs as positive examples.
      - Patches FAQ embeddings when evaluation finds factual errors.
      - Maintains an audit log of all embedding changes.
    """

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        qdrant_client: Optional[AsyncQdrantClient] = None,
        openai_client: Optional[AsyncOpenAI] = None,
    ) -> None:
        self._db = db
        self._qdrant = qdrant_client or AsyncQdrantClient(url=settings.qdrant_url)
        self._openai = openai_client or AsyncOpenAI(api_key=settings.openai_api_key)
        self._collection = settings.qdrant_collection_name

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_embedding(self, text: str) -> List[float]:
        """Generate an OpenAI embedding vector for the given text."""
        response = await self._openai.embeddings.create(
            model=settings.openai_embedding_model,
            input=text[:8000],  # guard against token limit
        )
        return response.data[0].embedding

    async def _ensure_collection(self) -> None:
        """Create the Qdrant collection if it does not already exist."""
        try:
            collections = await self._qdrant.get_collections()
            names = [c.name for c in collections.collections]
            if self._collection not in names:
                await self._qdrant.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(
                        size=settings.qdrant_embedding_dim,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info("Created Qdrant collection: %s", self._collection)
        except Exception as exc:
            logger.error("Error ensuring Qdrant collection: %s", exc)

    async def _upsert_point(
        self,
        point_id: str,
        vector: List[float],
        payload: Dict[str, Any],
    ) -> None:
        """Upsert a single point into Qdrant."""
        import hashlib

        # Qdrant point IDs must be unsigned ints or UUIDs
        numeric_id = int(hashlib.sha256(point_id.encode()).hexdigest()[:15], 16)
        point = PointStruct(id=numeric_id, vector=vector, payload=payload)
        await self._qdrant.upsert(
            collection_name=self._collection,
            points=[point],
        )

    def _extract_qa_pairs(self, messages: List[Dict]) -> List[Dict[str, str]]:
        """
        Extract question-answer pairs from a conversation message list.
        Pairs a lead (user) message with the immediately following agent response.
        """
        pairs: List[Dict[str, str]] = []
        for i, msg in enumerate(messages):
            if msg.get("role") == "user" and i + 1 < len(messages):
                next_msg = messages[i + 1]
                if next_msg.get("role") == "assistant":
                    question = msg.get("content", "").strip()
                    answer = next_msg.get("content", "").strip()
                    if question and answer:
                        pairs.append(
                            {
                                "question": question[:_MAX_PAIR_LENGTH],
                                "answer": answer[:_MAX_PAIR_LENGTH],
                            }
                        )
        return pairs

    async def _log_update(
        self,
        tenant_id: str,
        update_type: str,
        details: Dict[str, Any],
    ) -> None:
        """Append an entry to the embedding update audit log in MongoDB."""
        log_entry = {
            "tenant_id": tenant_id,
            "update_type": update_type,
            "details": details,
            "created_at": datetime.utcnow(),
        }
        await self._db[_LOG_COLLECTION].insert_one(log_entry)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def update_from_successful_conversation(
        self,
        conversation: Dict[str, Any],
        tenant_id: str,
    ) -> None:
        """
        Extract Q&A pairs from a successful conversation and add them to the
        Qdrant knowledge base as positive training examples.

        A conversation is considered successful if:
          - qualification_accuracy >= 0.8  (high qualification)
          - meeting_booked == True

        Args:
            conversation: Full conversation document.
            tenant_id:    Tenant identifier.
        """
        qualification_accuracy = float(
            conversation.get("qualification_accuracy", 0.0)
        )
        meeting_booked = bool(conversation.get("meeting_booked", False))

        if qualification_accuracy < 0.8 or not meeting_booked:
            logger.debug(
                "Conversation %s skipped for knowledge update "
                "(qualification_accuracy=%.2f, meeting_booked=%s)",
                conversation.get("conversation_id"),
                qualification_accuracy,
                meeting_booked,
            )
            return

        await self._ensure_collection()

        messages = conversation.get("messages", [])
        pairs = self._extract_qa_pairs(messages)

        if not pairs:
            logger.info(
                "No Q&A pairs extracted from conversation %s",
                conversation.get("conversation_id"),
            )
            return

        conversation_id = conversation.get("conversation_id", "unknown")
        upserted_count = 0

        for idx, pair in enumerate(pairs):
            combined = f"Q: {pair['question']}\nA: {pair['answer']}"
            try:
                vector = await self._get_embedding(combined)
                point_id = f"{tenant_id}:{conversation_id}:{idx}"
                payload = {
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "type": "successful_qa_example",
                    "question": pair["question"],
                    "answer": pair["answer"],
                    "qualification_accuracy": qualification_accuracy,
                    "created_at": datetime.utcnow().isoformat(),
                }
                await self._upsert_point(point_id, vector, payload)
                upserted_count += 1
            except Exception as exc:
                logger.error("Failed to upsert Q&A pair %d: %s", idx, exc)

        logger.info(
            "Added %d successful Q&A pairs from conversation %s to knowledge base",
            upserted_count,
            conversation_id,
        )

        await self._log_update(
            tenant_id=tenant_id,
            update_type="successful_conversation_ingestion",
            details={
                "conversation_id": conversation_id,
                "pairs_added": upserted_count,
                "qualification_accuracy": qualification_accuracy,
            },
        )

    async def update_faq_correction(
        self,
        mistake: Mistake,
        correction: str,
        tenant_id: str,
    ) -> None:
        """
        Update the Qdrant knowledge base with corrected FAQ information derived
        from a detected mistake.

        The old incorrect embedding is superseded by a new point encoding the
        correction. The payload is tagged so Agent A retrieves the updated answer.

        Args:
            mistake:    The Mistake object that revealed incorrect FAQ info.
            correction: The corrected answer / fact.
            tenant_id:  Tenant identifier.
        """
        await self._ensure_collection()

        # Build a corrected knowledge entry
        corrected_text = (
            f"CORRECTION: {correction}\n"
            f"Original issue: {mistake.description}\n"
            f"Do not say: {mistake.description}"
        )

        try:
            vector = await self._get_embedding(corrected_text)
            point_id = (
                f"{tenant_id}:faq_correction:{mistake.type.value}:"
                f"{hash(mistake.description) & 0xFFFFFFFF}"
            )
            payload = {
                "tenant_id": tenant_id,
                "type": "faq_correction",
                "mistake_type": mistake.type.value,
                "severity": mistake.severity.value,
                "original_issue": mistake.description,
                "correction": correction,
                "created_at": datetime.utcnow().isoformat(),
            }
            await self._upsert_point(point_id, vector, payload)
            logger.info(
                "FAQ correction upserted for mistake type %s (tenant: %s)",
                mistake.type.value,
                tenant_id,
            )

            await self._log_update(
                tenant_id=tenant_id,
                update_type="faq_correction",
                details={
                    "mistake_type": mistake.type.value,
                    "severity": mistake.severity.value,
                    "original_issue": mistake.description,
                    "correction": correction,
                },
            )

        except Exception as exc:
            logger.error(
                "Failed to upsert FAQ correction for %s: %s", mistake.type.value, exc
            )

    async def get_embedding_update_log(
        self, tenant_id: str
    ) -> List[Dict[str, Any]]:
        """
        Retrieve the full embedding update audit log for a tenant.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            List of log entry dicts ordered by most recent first.
        """
        cursor = (
            self._db[_LOG_COLLECTION]
            .find({"tenant_id": tenant_id})
            .sort("created_at", -1)
            .limit(200)
        )
        docs = await cursor.to_list(length=200)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return docs
