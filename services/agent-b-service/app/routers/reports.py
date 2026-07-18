"""
Reports router — Agent B REST API for business and agent performance reports.
Prefix: /api/v1/reports
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_db(request: Request):
    return request.app.state.db


def get_evaluator(request: Request):
    return request.app.state.evaluator


def get_producer(request: Request):
    return request.app.state.producer


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class GenerateReportRequest(BaseModel):
    tenant_id: str
    start_date: datetime
    end_date: datetime
    report_type: str = "full"  # "full" | "agent" | "business"


# ---------------------------------------------------------------------------
# Internal aggregation helpers
# ---------------------------------------------------------------------------

async def _business_metrics(
    db, tenant_id: str, since: datetime, until: datetime
) -> Dict[str, Any]:
    """
    Compute owner-facing business KPIs from evaluation data:
    meetings booked, conversation quality trends, ROI signals.
    """
    pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "created_at": {"$gte": since, "$lte": until},
            }
        },
        {
            "$facet": {
                "overview": [
                    {
                        "$group": {
                            "_id": None,
                            "total_conversations": {"$sum": 1},
                            "avg_score": {"$avg": "$overall_score"},
                            "avg_qualification_accuracy": {
                                "$avg": "$qualification_accuracy"
                            },
                            "improving_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$sentiment_trend", "improving"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "declining_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$sentiment_trend", "declining"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    }
                ],
                "score_by_day": [
                    {
                        "$group": {
                            "_id": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": "$created_at",
                                }
                            },
                            "avg_score": {"$avg": "$overall_score"},
                            "count": {"$sum": 1},
                        }
                    },
                    {"$sort": {"_id": 1}},
                ],
                "top_mistakes": [
                    {"$unwind": "$mistakes"},
                    {
                        "$group": {
                            "_id": "$mistakes.type",
                            "count": {"$sum": 1},
                            "avg_severity_weight": {
                                "$avg": {
                                    "$switch": {
                                        "branches": [
                                            {
                                                "case": {"$eq": ["$mistakes.severity", "critical"]},
                                                "then": 5,
                                            },
                                            {
                                                "case": {"$eq": ["$mistakes.severity", "high"]},
                                                "then": 4,
                                            },
                                            {
                                                "case": {"$eq": ["$mistakes.severity", "medium"]},
                                                "then": 3,
                                            },
                                            {
                                                "case": {"$eq": ["$mistakes.severity", "low"]},
                                                "then": 2,
                                            },
                                        ],
                                        "default": 1,
                                    }
                                }
                            },
                        }
                    },
                    {"$sort": {"count": -1}},
                    {"$limit": 10},
                ],
            }
        },
    ]

    results = await db["evaluation_reports"].aggregate(pipeline).to_list(length=None)
    facet = results[0] if results else {}
    overview = (facet.get("overview") or [{}])[0]
    overview.pop("_id", None)

    return {
        "overview": overview,
        "score_trend": facet.get("score_by_day", []),
        "top_mistake_types": facet.get("top_mistakes", []),
    }


async def _agent_metrics(
    db, tenant_id: str, since: datetime, until: datetime
) -> Dict[str, Any]:
    """
    Compute detailed agent performance metrics: learnings applied,
    rollback rates, category improvements.
    """
    eval_pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "created_at": {"$gte": since, "$lte": until},
            }
        },
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "avg_score": {"$avg": "$overall_score"},
                "applied_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$improvement_status", "applied"]},
                            1,
                            0,
                        ]
                    }
                },
                "failed_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$improvement_status", "failed"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
    ]

    learning_pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "created_at": {"$gte": since, "$lte": until},
            }
        },
        {
            "$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "avg_confidence": {"$avg": "$confidence_score"},
            }
        },
    ]

    import asyncio

    eval_results, learning_results = await asyncio.gather(
        db["evaluation_reports"].aggregate(eval_pipeline).to_list(length=None),
        db["learnings"].aggregate(learning_pipeline).to_list(length=None),
    )

    eval_summary = (eval_results or [{}])[0]
    eval_summary.pop("_id", None)

    learning_summary = {
        doc["_id"]: {
            "count": doc["count"],
            "avg_confidence": round(doc.get("avg_confidence", 0), 4),
        }
        for doc in learning_results
        if doc.get("_id")
    }

    return {
        "evaluation_summary": eval_summary,
        "learning_summary": learning_summary,
    }


# ---------------------------------------------------------------------------
# GET /business-performance
# ---------------------------------------------------------------------------

@router.get(
    "/business-performance",
    summary="Owner-facing business metrics report",
)
async def get_business_performance(
    tenant_id: str = Query(..., description="Tenant identifier"),
    days: int = Query(30, ge=1, le=365),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Return high-level business KPIs that a business owner cares about:
    conversation volume, quality scores, sentiment trends, top mistake types.
    """
    until = datetime.utcnow()
    since = until - timedelta(days=days)

    metrics = await _business_metrics(db, tenant_id, since, until)
    return {
        "tenant_id": tenant_id,
        "period": {
            "start": since.isoformat(),
            "end": until.isoformat(),
            "days": days,
        },
        **metrics,
    }


# ---------------------------------------------------------------------------
# GET /agent-performance
# ---------------------------------------------------------------------------

@router.get(
    "/agent-performance",
    summary="Detailed agent performance report",
)
async def get_agent_performance(
    tenant_id: str = Query(..., description="Tenant identifier"),
    days: int = Query(30, ge=1, le=365),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Return detailed metrics for the AI agent's performance:
    learning application rates, confidence scores, category breakdowns.
    """
    until = datetime.utcnow()
    since = until - timedelta(days=days)

    metrics = await _agent_metrics(db, tenant_id, since, until)
    return {
        "tenant_id": tenant_id,
        "period": {
            "start": since.isoformat(),
            "end": until.isoformat(),
            "days": days,
        },
        **metrics,
    }


# ---------------------------------------------------------------------------
# GET /improvement-timeline
# ---------------------------------------------------------------------------

@router.get(
    "/improvement-timeline",
    summary="Timeline of agent improvements",
)
async def get_improvement_timeline(
    tenant_id: str = Query(..., description="Tenant identifier"),
    days: int = Query(90, ge=1, le=365),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """
    Return a weekly aggregation of applied learnings alongside average
    evaluation scores to visualise improvement trajectory over time.
    """
    until = datetime.utcnow()
    since = until - timedelta(days=days)

    eval_pipeline = [
        {"$match": {"tenant_id": tenant_id, "created_at": {"$gte": since}}},
        {
            "$group": {
                "_id": {
                    "$dateToString": {"format": "%Y-W%V", "date": "$created_at"}
                },
                "avg_score": {"$avg": "$overall_score"},
                "conversations": {"$sum": 1},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    learning_pipeline = [
        {
            "$match": {
                "tenant_id": tenant_id,
                "applied_at": {"$gte": since, "$exists": True},
            }
        },
        {
            "$group": {
                "_id": {
                    "$dateToString": {"format": "%Y-W%V", "date": "$applied_at"}
                },
                "learnings_applied": {"$sum": 1},
                "avg_confidence": {"$avg": "$confidence_score"},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    import asyncio

    eval_docs, learning_docs = await asyncio.gather(
        db["evaluation_reports"].aggregate(eval_pipeline).to_list(length=None),
        db["learnings"].aggregate(learning_pipeline).to_list(length=None),
    )

    # Merge by week key
    weeks: Dict[str, Dict] = {}
    for doc in eval_docs:
        w = doc["_id"]
        weeks.setdefault(w, {"week": w})
        weeks[w]["avg_score"] = round(doc["avg_score"], 2)
        weeks[w]["conversations"] = doc["conversations"]

    for doc in learning_docs:
        w = doc["_id"]
        weeks.setdefault(w, {"week": w})
        weeks[w]["learnings_applied"] = doc["learnings_applied"]
        weeks[w]["avg_learning_confidence"] = round(doc["avg_confidence"], 4)

    return {
        "tenant_id": tenant_id,
        "period_days": days,
        "timeline": sorted(weeks.values(), key=lambda x: x["week"]),
    }


# ---------------------------------------------------------------------------
# POST /generate
# ---------------------------------------------------------------------------

@router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger manual report generation for a date range",
)
async def generate_report(
    body: GenerateReportRequest,
    producer=Depends(get_producer),
) -> Dict[str, Any]:
    """
    Trigger asynchronous generation of a full report for the specified
    date range and report type. Publishes a report.generated Kafka event.
    """
    report_id = str(uuid4())

    await producer.publish_report_generated(
        report_id=report_id,
        tenant_id=body.tenant_id,
        report_type=body.report_type,
        period_start=body.start_date.isoformat(),
        period_end=body.end_date.isoformat(),
    )

    logger.info(
        "Report generation triggered: %s type=%s tenant=%s",
        report_id,
        body.report_type,
        body.tenant_id,
    )

    return {
        "message": "Report generation triggered",
        "report_id": report_id,
        "tenant_id": body.tenant_id,
        "report_type": body.report_type,
        "period": {
            "start": body.start_date.isoformat(),
            "end": body.end_date.isoformat(),
        },
        "triggered_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /export/{format}
# ---------------------------------------------------------------------------

@router.get(
    "/export/{format}",
    summary="Export report as PDF or CSV",
)
async def export_report(
    format: str,
    tenant_id: str = Query(..., description="Tenant identifier"),
    days: int = Query(30, ge=1, le=365),
    db=Depends(get_db),
) -> Response:
    """
    Export evaluation and learning data as either CSV or JSON (PDF requires
    a rendering service; returns JSON placeholder for now).

    Supported formats: csv, json, pdf
    """
    if format not in ("csv", "json", "pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export format: {format!r}. Choose: csv, json, pdf",
        )

    until = datetime.utcnow()
    since = until - timedelta(days=days)

    # Fetch evaluation data
    eval_docs = (
        await db["evaluation_reports"]
        .find(
            {"tenant_id": tenant_id, "created_at": {"$gte": since}},
            {
                "conversation_id": 1,
                "lead_id": 1,
                "overall_score": 1,
                "qualification_accuracy": 1,
                "sentiment_trend": 1,
                "improvement_status": 1,
                "created_at": 1,
            },
        )
        .sort("created_at", -1)
        .to_list(length=1000)
    )

    for doc in eval_docs:
        doc["id"] = str(doc.pop("_id", ""))
        if "created_at" in doc:
            doc["created_at"] = doc["created_at"].isoformat()

    if format == "csv":
        output = io.StringIO()
        if eval_docs:
            writer = csv.DictWriter(output, fieldnames=list(eval_docs[0].keys()))
            writer.writeheader()
            writer.writerows(eval_docs)

        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="report_{tenant_id}_{days}d.csv"'
                )
            },
        )

    if format == "pdf":
        # PDF rendering requires a dedicated service (e.g. WeasyPrint / Puppeteer).
        # Return a JSON payload that the frontend can convert, or integrate a
        # PDF service URL here.
        logger.warning("PDF export requested — returning JSON payload as placeholder")
        return Response(
            content=json.dumps(
                {
                    "note": (
                        "PDF rendering requires an external PDF service. "
                        "Use the JSON export and process through your PDF renderer."
                    ),
                    "tenant_id": tenant_id,
                    "period_days": days,
                    "data": eval_docs,
                },
                default=str,
            ),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="report_{tenant_id}_{days}d.json"'
                )
            },
        )

    # Default: JSON
    return Response(
        content=json.dumps(
            {
                "tenant_id": tenant_id,
                "period": {
                    "start": since.isoformat(),
                    "end": until.isoformat(),
                    "days": days,
                },
                "total_records": len(eval_docs),
                "evaluations": eval_docs,
            },
            default=str,
        ),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="report_{tenant_id}_{days}d.json"'
            )
        },
    )
