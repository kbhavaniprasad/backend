"""
Agent router — /api/v1/agent

Endpoints:
  GET  /status                           Agent operational status
  GET  /knowledge                        List knowledge base documents
  POST /knowledge                        Add a document to the knowledge base
  PUT  /knowledge/{doc_id}               Update an existing knowledge document
  GET  /prompt-versions                  List all prompt scenarios and versions
  POST /prompt-versions/{version}/activate  Activate a prompt version for a scenario
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.core.rag_engine import RAGEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response Schemas
# ─────────────────────────────────────────────────────────────────────────────


class AddDocumentRequest(BaseModel):
    """Body for POST /api/v1/agent/knowledge."""

    tenant_id: str = Field(..., description="Tenant that owns this document.")
    content: str = Field(..., min_length=1, description="Raw text content of the document chunk.")
    source: str = Field(
        default="manual_upload",
        description="Origin of the document (e.g., 'website', 'faq', 'product_catalog').",
    )
    category: str | None = Field(
        default=None,
        description="Optional document category for filtering.",
    )
    doc_id: str | None = Field(
        default=None,
        description="Optional stable ID — generated if not provided.",
    )


class UpdateDocumentRequest(BaseModel):
    """Body for PUT /api/v1/agent/knowledge/{doc_id}."""

    tenant_id: str = Field(..., description="Tenant that owns this document.")
    content: str = Field(..., min_length=1, description="Replacement text content.")
    source: str | None = Field(default=None, description="Updated source identifier.")
    category: str | None = Field(default=None, description="Updated category.")


class ActivatePromptVersionRequest(BaseModel):
    """Body for POST /api/v1/agent/prompt-versions/{version}/activate."""

    scenario: str = Field(
        ...,
        description="Prompt scenario to update (e.g., 'initial_call', 'qualification').",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dependency helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_rag_engine(request: Request) -> RAGEngine:
    return request.app.state.rag_engine


def _get_prompt_manager(request: Request):  # type: ignore[return]
    return request.app.state.prompt_manager


def _get_settings(request: Request):  # type: ignore[return]
    return request.app.state.settings


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/status",
    response_model=dict[str, Any],
    summary="Agent operational status",
)
async def get_agent_status(request: Request) -> dict[str, Any]:
    """
    Return a snapshot of the agent's current operational metrics including
    active call count, queue depth, and service health indicators.
    """
    redis_client = request.app.state.redis

    # Gather stats concurrently for low latency
    active_leads_key = "agent:active_leads_count"
    queue_depth_key = "agent:queue_depth"

    active_calls, queue_depth = await asyncio.gather(
        redis_client.get(active_leads_key),
        redis_client.llen("agent:processing_queue"),
        return_exceptions=True,
    )

    # Handle Redis errors gracefully
    active_calls_val = int(active_calls) if isinstance(active_calls, (bytes, str, int)) else 0
    queue_depth_val = int(queue_depth) if isinstance(queue_depth, int) else 0

    cfg = _get_settings(request)

    return {
        "status": "operational",
        "service": "agent-a-service",
        "environment": cfg.environment,
        "active_calls": active_calls_val,
        "queue_depth": queue_depth_val,
        "max_concurrent_calls": cfg.max_concurrent_calls,
        "capacity_remaining": max(0, cfg.max_concurrent_calls - active_calls_val),
        "kafka_consumer": "running",
    }


@router.get(
    "/knowledge",
    response_model=dict[str, Any],
    summary="List knowledge base documents",
)
async def list_knowledge(
    tenant_id: Annotated[str, Query(description="Tenant identifier")],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    rag_engine: RAGEngine = Depends(_get_rag_engine),
) -> dict[str, Any]:
    """
    Return a paginated list of all documents stored in the tenant's Qdrant
    knowledge base collection.
    """
    try:
        documents = await rag_engine.list_documents(
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.error("Failed to list knowledge documents: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve knowledge base: {exc}",
        )

    return {
        "tenant_id": tenant_id,
        "documents": documents,
        "count": len(documents),
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/knowledge",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Add a document to the knowledge base",
)
async def add_knowledge_document(
    body: AddDocumentRequest,
    rag_engine: RAGEngine = Depends(_get_rag_engine),
) -> dict[str, Any]:
    """
    Embed and store a new document in the tenant's Qdrant knowledge base.
    Returns the stable doc_id assigned to the new document.
    """
    metadata: dict[str, Any] = {"source": body.source}
    if body.category:
        metadata["category"] = body.category

    try:
        doc_id = await rag_engine.add_document(
            tenant_id=body.tenant_id,
            content=body.content,
            metadata=metadata,
            doc_id=body.doc_id,
        )
    except Exception as exc:
        logger.error("Failed to add knowledge document: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store document: {exc}",
        )

    logger.info(
        "Knowledge document added | tenant=%s doc_id=%s",
        body.tenant_id,
        doc_id,
    )
    return {
        "doc_id": doc_id,
        "tenant_id": body.tenant_id,
        "content_length": len(body.content),
        "source": body.source,
        "message": "Document successfully embedded and stored.",
    }


@router.put(
    "/knowledge/{doc_id}",
    response_model=dict[str, Any],
    summary="Update an existing knowledge base document",
)
async def update_knowledge_document(
    doc_id: str,
    body: UpdateDocumentRequest,
    rag_engine: RAGEngine = Depends(_get_rag_engine),
) -> dict[str, Any]:
    """
    Replace the content (and optionally metadata) of an existing knowledge
    document.  The document is re-embedded with the new content.
    """
    metadata: dict[str, Any] = {}
    if body.source:
        metadata["source"] = body.source
    if body.category:
        metadata["category"] = body.category

    try:
        await rag_engine.update_knowledge(
            tenant_id=body.tenant_id,
            doc_id=doc_id,
            new_content=body.content,
            metadata=metadata or None,
        )
    except Exception as exc:
        logger.error(
            "Failed to update knowledge document '%s': %s", doc_id, exc, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update document: {exc}",
        )

    logger.info(
        "Knowledge document updated | tenant=%s doc_id=%s",
        body.tenant_id,
        doc_id,
    )
    return {
        "doc_id": doc_id,
        "tenant_id": body.tenant_id,
        "content_length": len(body.content),
        "message": "Document successfully re-embedded and updated.",
    }


@router.get(
    "/prompt-versions",
    response_model=dict[str, Any],
    summary="List all prompt scenarios and their versions",
)
async def list_prompt_versions(
    request: Request,
) -> dict[str, Any]:
    """
    Return all registered prompt scenarios, their current version identifiers,
    and which version is currently active.
    """
    pm = _get_prompt_manager(request)
    versions = pm.list_prompt_versions()

    return {
        "manager_version": pm.prompt_version,
        "scenarios": versions,
        "total": len(versions),
    }


@router.post(
    "/prompt-versions/{version}/activate",
    response_model=dict[str, Any],
    summary="Activate a prompt version for a scenario",
)
async def activate_prompt_version(
    version: str,
    body: ActivatePromptVersionRequest,
    request: Request,
) -> dict[str, Any]:
    """
    Activate a specific prompt version for the given scenario.

    Note: This is an in-memory operation on the current process.  In a
    multi-replica deployment, use a distributed configuration store (Redis /
    MongoDB) to propagate the change to all replicas.
    """
    pm = _get_prompt_manager(request)

    try:
        activated = pm.activate_prompt_version(scenario=body.scenario, version=version)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if not activated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prompt version '{version}' not found for scenario '{body.scenario}'.",
        )

    logger.info(
        "Prompt version activated | scenario=%s version=%s",
        body.scenario,
        version,
    )
    return {
        "scenario": body.scenario,
        "activated_version": version,
        "message": f"Prompt version '{version}' is now active for scenario '{body.scenario}'.",
    }
