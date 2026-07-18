"""
Learnings router — Agent B REST API for managing learning records.
Prefix: /api/v1/learnings
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.config import get_settings
from app.models.evaluation import LearningStatus, SeverityLevel

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/learnings", tags=["Learnings"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_db(request: Request):
    return request.app.state.db


def get_learning_gen(request: Request):
    return request.app.state.learning_generator


def _object_id_or_400(id_str: str) -> ObjectId:
    if not ObjectId.is_valid(id_str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ID format: {id_str!r}",
        )
    return ObjectId(id_str)


def _serialize_doc(doc: Dict) -> Dict:
    doc["id"] = str(doc.pop("_id", ""))
    return doc


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RollbackRequest(BaseModel):
    reason: str


# ---------------------------------------------------------------------------
# GET /  — List all learnings
# ---------------------------------------------------------------------------

@router.get(
    "/",
    summary="List all learnings",
)
async def list_learnings(
    tenant_id: str = Query(..., description="Tenant identifier"),
    status_filter: Optional[LearningStatus] = Query(None, alias="status"),
    category: Optional[str] = Query(None),
    severity: Optional[SeverityLevel] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(
        settings.default_page_size, ge=1, le=settings.max_page_size
    ),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Return a paginated, optionally filtered list of Learning records for a tenant.
    Filter by status, category, and/or severity.
    """
    import asyncio

    query: Dict[str, Any] = {"tenant_id": tenant_id}
    if status_filter:
        query["status"] = status_filter.value
    if category:
        query["category"] = {"$regex": category, "$options": "i"}
    if severity:
        query["severity"] = severity.value

    skip = (page - 1) * page_size

    total, docs = await asyncio.gather(
        db["learnings"].count_documents(query),
        db["learnings"]
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
        "total_pages": -(-total // page_size),
        "items": [_serialize_doc(d) for d in docs],
    }


# ---------------------------------------------------------------------------
# GET /timeline  — Agent evolution timeline
# ---------------------------------------------------------------------------

@router.get(
    "/timeline",
    summary="Agent evolution timeline (learnings over time)",
)
async def get_timeline(
    tenant_id: str = Query(..., description="Tenant identifier"),
    days: int = Query(90, ge=1, le=365),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Return a chronological timeline of learnings grouped by day, showing
    category distribution and status for visualising agent evolution.
    """
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)
    pipeline = [
        {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": since}}},
        {
            "$group": {
                "_id": {
                    "date": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$created_at",
                        }
                    },
                    "category": "$category",
                    "status": "$status",
                },
                "count": {"$sum": 1},
                "avg_confidence": {"$avg": "$confidence_score"},
            }
        },
        {"$sort": {"_id.date": 1}},
    ]

    docs = await db["learnings"].aggregate(pipeline).to_list(length=None)

    # Restructure for timeline chart consumption
    timeline: Dict[str, Any] = {}
    for doc in docs:
        date = doc["_id"]["date"]
        if date not in timeline:
            timeline[date] = {"date": date, "total": 0, "categories": {}, "statuses": {}}
        timeline[date]["total"] += doc["count"]
        cat = doc["_id"]["category"]
        st = doc["_id"]["status"]
        timeline[date]["categories"][cat] = (
            timeline[date]["categories"].get(cat, 0) + doc["count"]
        )
        timeline[date]["statuses"][st] = (
            timeline[date]["statuses"].get(st, 0) + doc["count"]
        )

    return {
        "tenant_id": tenant_id,
        "period_days": days,
        "timeline": sorted(timeline.values(), key=lambda x: x["date"]),
    }


# ---------------------------------------------------------------------------
# GET /confidence-improvements  — Confidence score chart data
# ---------------------------------------------------------------------------

@router.get(
    "/confidence-improvements",
    summary="Confidence score improvements over time",
)
async def get_confidence_improvements(
    tenant_id: str = Query(..., description="Tenant identifier"),
    days: int = Query(60, ge=1, le=365),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Return chart-ready data showing how learning confidence scores have
    evolved over time, grouped by week.
    """
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)
    pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "created_at": {"$gte": since},
                "status": {"$in": ["applied", "verified"]},
            }
        },
        {
            "$group": {
                "_id": {
                    "$dateToString": {"format": "%Y-W%V", "date": "$created_at"}
                },
                "avg_confidence": {"$avg": "$confidence_score"},
                "max_confidence": {"$max": "$confidence_score"},
                "min_confidence": {"$min": "$confidence_score"},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    docs = await db["learnings"].aggregate(pipeline).to_list(length=None)

    return {
        "tenant_id": tenant_id,
        "period_days": days,
        "data_points": [
            {
                "week": doc["_id"],
                "avg_confidence": round(doc["avg_confidence"], 4),
                "max_confidence": round(doc["max_confidence"], 4),
                "min_confidence": round(doc["min_confidence"], 4),
                "count": doc["count"],
            }
            for doc in docs
        ],
    }


# ---------------------------------------------------------------------------
# GET /mistakes-fixed  — Fixed mistakes before/after
# ---------------------------------------------------------------------------

@router.get(
    "/mistakes-fixed",
    summary="List fixed mistakes with before/after behaviour",
)
async def get_mistakes_fixed(
    tenant_id: str = Query(..., description="Tenant identifier"),
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.default_page_size, ge=1, le=settings.max_page_size),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Return learnings that have been applied or verified, showing the
    old_behavior vs new_behavior diff for each.
    """
    import asyncio

    query = {
        "tenant_id": tenant_id,
        "status": {"$in": [LearningStatus.applied.value, LearningStatus.verified.value]},
    }
    skip = (page - 1) * page_size

    total, docs = await asyncio.gather(
        db["learnings"].count_documents(query),
        db["learnings"]
        .find(
            query,
            {
                "title": 1,
                "category": 1,
                "severity": 1,
                "old_behavior": 1,
                "new_behavior": 1,
                "confidence_score": 1,
                "status": 1,
                "applied_at": 1,
                "verified_at": 1,
            },
        )
        .sort("applied_at", -1)
        .skip(skip)
        .limit(page_size)
        .to_list(length=page_size),
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": -(-total // page_size),
        "items": [_serialize_doc(d) for d in docs],
    }


# ---------------------------------------------------------------------------
# GET /{learning_id}  — Get learning detail
# ---------------------------------------------------------------------------

@router.get(
    "/{learning_id}",
    summary="Get learning detail",
)
async def get_learning(
    learning_id: str,
    db=Depends(get_db),
) -> Dict[str, Any]:
    """Return the full Learning document by its MongoDB ID."""
    oid = _object_id_or_400(learning_id)
    doc = await db["learnings"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Learning {learning_id!r} not found.",
        )
    return _serialize_doc(doc)


# ---------------------------------------------------------------------------
# POST /{learning_id}/apply  — Manually apply a learning
# ---------------------------------------------------------------------------

@router.post(
    "/{learning_id}/apply",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Manually trigger learning application",
)
async def apply_learning(
    learning_id: str,
    db=Depends(get_db),
    learning_gen=Depends(get_learning_gen),
) -> Dict[str, Any]:
    """
    Manually trigger the application of a pending learning to Agent A's
    prompt system.  Returns 202 and initiates the process asynchronously.
    """
    oid = _object_id_or_400(learning_id)
    doc = await db["learnings"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Learning {learning_id!r} not found.",
        )

    if doc.get("status") == LearningStatus.applied.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Learning is already in 'applied' status.",
        )

    from app.models.evaluation import Learning

    doc["_id"] = str(doc["_id"])
    learning = Learning(**doc)

    import asyncio

    asyncio.create_task(
        learning_gen.apply_learning(
            learning,
            agent_a_service_url=settings.agent_a_service_url,
        ),
        name=f"apply-learning-{learning_id}",
    )

    logger.info("Manual apply triggered for learning %s", learning_id)
    return {
        "message": "Apply triggered",
        "learning_id": learning_id,
        "triggered_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# POST /{learning_id}/rollback  — Rollback a learning
# ---------------------------------------------------------------------------

@router.post(
    "/{learning_id}/rollback",
    summary="Rollback a learning with a reason",
)
async def rollback_learning(
    learning_id: str,
    body: RollbackRequest,
    learning_gen=Depends(get_learning_gen),
) -> Dict[str, Any]:
    """
    Roll back an applied learning, reverting Agent A's prompt to the previous
    version and recording the rollback reason.
    """
    success = await learning_gen.rollback_learning(
        learning_id=learning_id,
        reason=body.reason,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rollback failed. Ensure the learning exists and is in 'applied' status.",
        )

    logger.info("Rollback successful for learning %s", learning_id)
    return {
        "message": "Rollback successful",
        "learning_id": learning_id,
        "reason": body.reason,
        "rolled_back_at": datetime.utcnow().isoformat(),
    }
