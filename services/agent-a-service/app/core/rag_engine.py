"""
RAGEngine — Retrieval-Augmented Generation engine backed by Qdrant vector DB.

Responsibilities:
- Embed text queries using OpenAI's text-embedding-ada-002 model.
- Search per-tenant Qdrant collections for relevant knowledge chunks.
- Upsert new documents (with embeddings) into the tenant's collection.
- Support document updates.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
    UpdateStatus,
)

logger = logging.getLogger(__name__)

# Dimensionality of text-embedding-ada-002
_ADA_002_DIM = 1536


def _collection_name(tenant_id: str) -> str:
    """Return the Qdrant collection name for a tenant."""
    return f"tenant_{tenant_id}_knowledge"


class RAGEngine:
    """
    Retrieval-Augmented Generation engine.

    Each tenant's knowledge is stored in an isolated Qdrant collection so that
    knowledge from one tenant never leaks to another.

    Example::

        engine = RAGEngine(qdrant_url="http://qdrant:6333", openai_api_key="sk-...")
        await engine.initialize()
        docs = await engine.retrieve_context("tenant_abc", "What is your refund policy?")
    """

    def __init__(self, qdrant_url: str, openai_api_key: str, qdrant_api_key: str | None = None) -> None:
        self._qdrant_url = qdrant_url
        self._qdrant_api_key = qdrant_api_key
        self._openai_api_key = openai_api_key
        self._qdrant: AsyncQdrantClient | None = None
        self._openai: AsyncOpenAI | None = None
        self._initialized = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def initialize(self) -> None:
        """
        Create the Qdrant and OpenAI client instances.
        Must be called once before any other method.
        """
        logger.info("Initializing RAGEngine (qdrant_url=%s)…", self._qdrant_url)

        self._qdrant = AsyncQdrantClient(
            url=self._qdrant_url,
            api_key=self._qdrant_api_key,
            timeout=30,
        )
        self._openai = AsyncOpenAI(api_key=self._openai_api_key)
        self._initialized = True
        logger.info("RAGEngine initialized successfully.")

    async def close(self) -> None:
        """Release underlying client resources."""
        if self._qdrant:
            await self._qdrant.close()
        self._initialized = False

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _ensure_initialized(self) -> None:
        if not self._initialized or self._qdrant is None or self._openai is None:
            raise RuntimeError(
                "RAGEngine.initialize() must be awaited before calling this method."
            )

    async def _embed(self, text: str) -> list[float]:
        """
        Embed a text string using OpenAI text-embedding-ada-002.

        Returns:
            List of 1536 floats.
        """
        assert self._openai is not None  # guaranteed by _ensure_initialized
        response = await self._openai.embeddings.create(
            model="text-embedding-ada-002",
            input=text,
        )
        return response.data[0].embedding

    async def _ensure_collection(self, tenant_id: str) -> None:
        """
        Create the tenant's Qdrant collection if it does not exist yet.
        Idempotent — safe to call on every operation.
        """
        assert self._qdrant is not None
        collection = _collection_name(tenant_id)

        existing = {c.name for c in (await self._qdrant.get_collections()).collections}
        if collection not in existing:
            logger.info("Creating Qdrant collection '%s'.", collection)
            await self._qdrant.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=_ADA_002_DIM, distance=Distance.COSINE),
            )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def retrieve_context(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.70,
    ) -> list[dict[str, Any]]:
        """
        Embed *query* and retrieve the top-k most relevant knowledge chunks
        from the tenant's Qdrant collection.

        Args:
            tenant_id:       Tenant identifier.
            query:           The user's message or search query.
            top_k:           Maximum number of results to return.
            score_threshold: Minimum cosine similarity score (0–1) for a result
                             to be included.  Filters out low-relevance chunks.

        Returns:
            List of dicts, each with keys: ``content``, ``score``, ``source``,
            ``doc_id``.  Empty list if nothing relevant is found.
        """
        self._ensure_initialized()
        collection = _collection_name(tenant_id)

        # Ensure collection exists before searching (lazy creation)
        await self._ensure_collection(tenant_id)

        query_vector = await self._embed(query)

        try:
            results = await self._qdrant.search(  # type: ignore[union-attr]
                collection_name=collection,
                query_vector=query_vector,
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
            )
        except Exception as exc:
            logger.error(
                "Qdrant search failed for tenant '%s': %s", tenant_id, exc, exc_info=True
            )
            return []

        docs: list[dict[str, Any]] = []
        for hit in results:
            payload = hit.payload or {}
            docs.append(
                {
                    "doc_id": str(hit.id),
                    "content": payload.get("content", ""),
                    "score": round(hit.score, 4),
                    "source": payload.get("source", "unknown"),
                    "metadata": {k: v for k, v in payload.items() if k not in {"content", "source"}},
                }
            )

        logger.debug(
            "RAG retrieve | tenant=%s query_len=%d results=%d",
            tenant_id,
            len(query),
            len(docs),
        )
        return docs

    async def add_document(
        self,
        tenant_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
    ) -> str:
        """
        Embed *content* and upsert it into the tenant's Qdrant collection.

        Args:
            tenant_id: Tenant identifier.
            content:   Raw text content of the document chunk.
            metadata:  Optional extra fields stored alongside the vector
                       (e.g. ``{"source": "website", "category": "pricing"}``).
            doc_id:    Optional stable identifier for the document.  If not
                       provided a UUID is generated.

        Returns:
            The ``doc_id`` of the inserted / updated point.
        """
        self._ensure_initialized()
        await self._ensure_collection(tenant_id)
        collection = _collection_name(tenant_id)

        effective_id = doc_id or str(uuid.uuid4())
        payload: dict[str, Any] = {"content": content, "source": "manual_upload"}
        if metadata:
            payload.update(metadata)

        vector = await self._embed(content)

        # Qdrant requires numeric point IDs when using integer IDs.
        # We derive a deterministic integer from the UUID/string via MD5.
        numeric_id = int(hashlib.md5(effective_id.encode()).hexdigest(), 16) % (2**63)

        await self._qdrant.upsert(  # type: ignore[union-attr]
            collection_name=collection,
            points=[
                PointStruct(
                    id=numeric_id,
                    vector=vector,
                    payload={**payload, "_doc_id": effective_id},
                )
            ],
        )
        logger.info(
            "Document upserted | tenant=%s doc_id=%s content_len=%d",
            tenant_id,
            effective_id,
            len(content),
        )
        return effective_id

    async def update_knowledge(
        self,
        tenant_id: str,
        doc_id: str,
        new_content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Replace an existing document's content (and optionally metadata) by
        re-embedding and upserting under the same ``doc_id``.

        Args:
            tenant_id:   Tenant identifier.
            doc_id:      The document's stable identifier.
            new_content: Replacement text content.
            metadata:    Optional updated metadata fields.
        """
        await self.add_document(
            tenant_id=tenant_id,
            content=new_content,
            metadata=metadata,
            doc_id=doc_id,
        )
        logger.info(
            "Knowledge updated | tenant=%s doc_id=%s",
            tenant_id,
            doc_id,
        )

    async def list_documents(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Page through all documents in a tenant's collection.

        Returns:
            List of payload dicts (includes ``_doc_id``, ``content``, ``source``).
        """
        self._ensure_initialized()
        await self._ensure_collection(tenant_id)
        collection = _collection_name(tenant_id)

        records, _ = await self._qdrant.scroll(  # type: ignore[union-attr]
            collection_name=collection,
            limit=limit,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        return [
            {
                "doc_id": r.payload.get("_doc_id", str(r.id)) if r.payload else str(r.id),
                "content": r.payload.get("content", "") if r.payload else "",
                "source": r.payload.get("source", "unknown") if r.payload else "unknown",
                "metadata": {
                    k: v
                    for k, v in (r.payload or {}).items()
                    if k not in {"content", "source", "_doc_id"}
                },
            }
            for r in records
        ]
