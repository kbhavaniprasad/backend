"""
Evaluations router — Agent B REST API for evaluation reports and performance metrics.
Prefix: /api/v1/evaluations
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.config import get_settings
from app.models.evaluation import EvaluationReport, MistakeType, SeverityLevel

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/evaluations", tags=["Evaluations"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_db(request: Request):
    """Extract Motor DB from app state."""
    return request.app.state.db


def get_evaluator(request: Request):
    """Extract PerformanceEvaluator from app state."""
    return request.app.state.evaluator


def _object_id_or_400(id_str: str) -> ObjectId:
    """Convert string to ObjectId, raising HTTP 400 on invalid format."""
    if not ObjectId.is_valid(id_str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ID format: {id_str!r}",
        )
    return ObjectId(id_str)


def _serialize_doc(doc: Dict) -> Dict:
    """Convert MongoDB document to JSON-serialisable dict."""
    doc["id"] = str(doc.pop("_id", ""))
    return doc


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

@router.get(
    "/",
    summary="List evaluation reports for a tenant",
    response_description="Paginated list of evaluation reports",
)
async def list_evaluations(
    tenant_id: str = Query(..., description="Tenant identifier"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(
        settings.default_page_size,
        ge=1,
        le=settings.max_page_size,
        description="Items per page",
    ),
    min_score: Optional[float] = Query(None, ge=0, le=10),
    max_score: Optional[float] = Query(None, ge=0, le=10),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Return a paginated list of EvaluationReports for the given tenant.
    Optionally filter by score range.
    """
    query: Dict[str, Any] = {"tenant_id": tenant_id}
    score_filter: Dict = {}
    if min_score is not None:
        score_filter["$gte"] = min_score
    if max_score is not None:
        score_filter["$lte"] = max_score
    if score_filter:
        query["overall_score"] = score_filter

    skip = (page - 1) * page_size

    total, docs = await asyncio.gather(
        db["evaluation_reports"].count_documents(query),
        db["evaluation_reports"]
        .find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(page_size)
        .to_list(length=page_size),
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": -(-total // page_size),  # ceiling division
        "items": [_serialize_doc(d) for d in docs],
    }


# ---------------------------------------------------------------------------
# GET /performance-summary (must be before /{evaluation_id})
# ---------------------------------------------------------------------------

@router.get(
    "/performance-summary",
    summary="Aggregate performance metrics for a tenant",
)
async def get_performance_summary(
    tenant_id: str = Query(..., description="Tenant identifier"),
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    evaluator=Depends(get_evaluator),
) -> Dict[str, Any]:
    """Return aggregated performance metrics across recent evaluations."""
    return await evaluator.get_agent_performance_summary(tenant_id=tenant_id, days=days)


# ---------------------------------------------------------------------------
# GET /mistakes
# ---------------------------------------------------------------------------

@router.get(
    "/mistakes",
    summary="List all mistakes across evaluations",
)
async def list_mistakes(
    tenant_id: str = Query(..., description="Tenant identifier"),
    mistake_type: Optional[MistakeType] = Query(None),
    severity: Optional[SeverityLevel] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Retrieve a flattened, paginated list of individual mistakes across all
    evaluations for a tenant, with optional filtering by type and severity.
    """
    match_stage: Dict[str, Any] = {"tenant_id": tenant_id}
    mistake_match: Dict[str, Any] = {}
    if mistake_type:
        mistake_match["mistakes.type"] = mistake_type.value
    if severity:
        mistake_match["mistakes.severity"] = severity.value

    pipeline = [
        {"$match": match_stage},
        {"$unwind": "$mistakes"},
        *(
            [{"$match": {"mistakes.type": mistake_type.value}}]
            if mistake_type
            else []
        ),
        *(
            [{"$match": {"mistakes.severity": severity.value}}]
            if severity
            else []
        ),
        {"$sort": {"created_at": -1}},
        {
            "$facet": {
                "total": [{"$count": "count"}],
                "items": [
                    {"$skip": (page - 1) * page_size},
                    {"$limit": page_size},
                    {
                        "$project": {
                            "evaluation_id": {"$toString": "$_id"},
                            "conversation_id": 1,
                            "tenant_id": 1,
                            "mistake": "$mistakes",
                            "created_at": 1,
                        }
                    },
                ],
            }
        },
    ]

    results = await db["evaluation_reports"].aggregate(pipeline).to_list(length=None)
    facet = results[0] if results else {}
    total_val = (facet.get("total") or [{"count": 0}])[0].get("count", 0)
    items = facet.get("items", [])
    for item in items:
        item.pop("_id", None)

    return {
        "total": total_val,
        "page": page,
        "page_size": page_size,
        "total_pages": -(-total_val // page_size),
        "items": items,
    }


# ---------------------------------------------------------------------------
# GET /conversation/{conversation_id}
# ---------------------------------------------------------------------------

@router.get(
    "/conversation/{conversation_id}",
    summary="Get evaluation for a specific conversation",
)
async def get_evaluation_by_conversation(
    conversation_id: str,
    tenant_id: str = Query(..., description="Tenant identifier"),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """Return the evaluation report for a specific conversation."""
    doc = await db["evaluation_reports"].find_one(
        {"conversation_id": conversation_id, "tenant_id": tenant_id}
    )
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation for conversation {conversation_id!r} not found.",
        )
    return _serialize_doc(doc)


# ---------------------------------------------------------------------------
# GET /{evaluation_id}
# ---------------------------------------------------------------------------

@router.get(
    "/{evaluation_id}",
    summary="Get a full evaluation report by ID",
)
async def get_evaluation(
    evaluation_id: str,
    db=Depends(get_db),
) -> Dict[str, Any]:
    """Return the full EvaluationReport document by its MongoDB ID."""
    oid = _object_id_or_400(evaluation_id)
    doc = await db["evaluation_reports"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation {evaluation_id!r} not found.",
        )
    return _serialize_doc(doc)


# ---------------------------------------------------------------------------
# POST /{conversation_id}/re-evaluate
# ---------------------------------------------------------------------------

@router.post(
    "/{conversation_id}/re-evaluate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger re-evaluation of a conversation",
)
async def re_evaluate_conversation(
    conversation_id: str,
    tenant_id: str = Query(..., description="Tenant identifier"),
    request: Request = None,
    db=Depends(get_db),
    evaluator=Depends(get_evaluator),
) -> Dict[str, Any]:
    """
    Trigger a fresh evaluation of an already-completed conversation.
    The original conversation payload is retrieved from the evaluation record
    and passed back through the evaluation pipeline.
    """
    # Find the most recent evaluation for this conversation to get its data
    doc = await db["evaluation_reports"].find_one(
        {"conversation_id": conversation_id, "tenant_id": tenant_id},
        sort=[("created_at", -1)],
    )
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existing evaluation for conversation {conversation_id!r}.",
        )

    # Retrieve original conversation payload from the lead service if available
    conversation_payload = doc.get("_raw_conversation") or {
        "conversation_id": conversation_id,
        "tenant_id": tenant_id,
        "lead_id": doc.get("lead_id", ""),
        "messages": doc.get("messages", []),
        "faq_context": doc.get("faq_context", ""),
    }

    # Fire re-evaluation asynchronously
    asyncio.create_task(
        evaluator.evaluate_conversation(conversation_payload),
        name=f"re-evaluate-{conversation_id}",
    )

    logger.info(
        "Re-evaluation triggered for conversation %s (tenant: %s)",
        conversation_id,
        tenant_id,
    )

    return {
        "message": "Re-evaluation triggered",
        "conversation_id": conversation_id,
        "tenant_id": tenant_id,
        "triggered_at": datetime.utcnow().isoformat(),
    }
