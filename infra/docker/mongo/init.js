// MongoDB initialization script for AI Lead Platform

db = db.getSiblingDB('ai_platform');

// ── Collections with Indexes ─────────────────────────────────────────────────

// Conversations / Calls
db.calls.createIndex({ "call_id": 1 }, { unique: true });
db.calls.createIndex({ "lead_id": 1 });
db.calls.createIndex({ "tenant_id": 1 });
db.calls.createIndex({ "tenant_id": 1, "status": 1 });
db.calls.createIndex({ "created_at": -1 });
db.calls.createIndex({ "call_analysis.call_successful": 1 });

// Evaluation Reports (Agent B)
db.evaluation_reports.createIndex({ "conversation_id": 1 }, { unique: true });
db.evaluation_reports.createIndex({ "tenant_id": 1 });
db.evaluation_reports.createIndex({ "lead_id": 1 });
db.evaluation_reports.createIndex({ "created_at": -1 });
db.evaluation_reports.createIndex({ "overall_score": 1 });
db.evaluation_reports.createIndex({ "tenant_id": 1, "created_at": -1 });

// Learnings
db.learnings.createIndex({ "tenant_id": 1 });
db.learnings.createIndex({ "status": 1 });
db.learnings.createIndex({ "severity": 1 });
db.learnings.createIndex({ "created_at": -1 });
db.learnings.createIndex({ "tenant_id": 1, "status": 1, "created_at": -1 });

// Agent Memory (RAG context per tenant)
db.agent_memory.createIndex({ "tenant_id": 1 });
db.agent_memory.createIndex({ "doc_id": 1 }, { unique: true });
db.agent_memory.createIndex({ "tenant_id": 1, "category": 1 });

// Prompt Versions
db.prompt_versions.createIndex({ "tenant_id": 1, "version": -1 });
db.prompt_versions.createIndex({ "is_active": 1 });

print("✅ MongoDB indexes created successfully");
