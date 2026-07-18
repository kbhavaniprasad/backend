# AI Lead Engagement Platform — Architecture Document

## 1. High-Level System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              LEAD SOURCES                                         │
│  Facebook Ads │ Google Ads │ LinkedIn │ Website Form │ WhatsApp │ Instagram │ CRM │
└──────────────────────────────┬───────────────────────────────────────────────────┘
                               │ Webhooks / API Calls
                    ┌──────────▼──────────┐
                    │    API Gateway       │  NestJS — Port 3000
                    │  JWT Auth │ Rate     │  NGINX reverse proxy
                    │  Limit │ Tenant Route│
                    └──────────┬──────────┘
                               │ gRPC / HTTP
        ┌──────────────────────┼──────────────────────────┐
        │                      │                           │
┌───────▼──────┐    ┌──────────▼──────┐    ┌─────────────▼──────┐
│ Auth Service │    │  Lead Service    │    │  Analytics Service  │
│  FastAPI     │    │  FastAPI + Kafka │    │  FastAPI            │
│  PostgreSQL  │    │  PostgreSQL      │    │  PostgreSQL+MongoDB │
└──────────────┘    └──────────┬──────┘    └────────────────────┘
                               │
                        Kafka: lead.created
                               │
                    ┌──────────▼──────────┐
                    │   Voice Service      │  FastAPI — Port 8005
                    │   Retell AI Client   │◄─── Retell Webhooks
                    │   Kafka Consumer     │     (call_started/ended/analyzed)
                    └──────────┬──────────┘
                               │  Retell AI Platform
                    ┌──────────▼──────────────────────────┐
                    │         RETELL AI                     │
                    │  ┌─────────────────────────────┐    │
                    │  │ Agent ID: agent_cbc4d9d...   │    │
                    │  │ STT → LLM (GPT-4o) → TTS    │    │
                    │  │ Real-time conversation AI    │    │
                    │  └─────────────────────────────┘    │
                    │         ↕ PSTN via Twilio             │
                    └──────────┬──────────────────────────┘
                               │ Lead's Phone
                               │
                        Kafka: conversation.completed
                               │
                    ┌──────────▼──────────┐
                    │  Agent A Service     │  FastAPI — Port 8003
                    │  LangChain + GPT-4o  │  (Chat/WhatsApp channels)
                    │  RAG Engine (Qdrant) │
                    │  Qualification Engine│
                    └──────────┬──────────┘
                               │
                        Kafka: conversation.completed
                               │
                    ┌──────────▼──────────┐
                    │  Agent B Service     │  FastAPI — Port 8004
                    │  AI Manager Agent    │  Evaluates every conversation
                    │  Transcript Analyzer │  Detects mistakes + learns
                    │  Learning Generator  │  Updates Retell Agent prompt
                    │  Knowledge Updater   │  Self-improving feedback loop
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                 │
     ┌────────▼──────┐ ┌──────▼──────┐ ┌───────▼──────┐
     │  MongoDB       │ │   Qdrant    │ │  Dashboard    │
     │  Evaluations   │ │  Vector DB  │ │  Notifications│
     │  Learnings     │ │  Knowledge  │ │  (WebSocket)  │
     └───────────────┘ └─────────────┘ └──────────────┘
```

---

## 2. Retell AI Integration Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     RETELL AI PLATFORM                    │
│                                                           │
│  Agent ID: agent_cbc4d9dffbfd3df155cccb4828              │
│                                                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │            Retell AI Voice Pipeline               │   │
│  │                                                    │   │
│  │  Lead Phone → [Twilio PSTN] → Retell STT          │   │
│  │                                      ↓             │   │
│  │                              Text transcript       │   │
│  │                                      ↓             │   │
│  │                         GPT-4o LLM processing      │   │
│  │                    (with injected lead context)     │   │
│  │                                      ↓             │   │
│  │                           AI response text         │   │
│  │                                      ↓             │   │
│  │                         Retell TTS synthesis        │   │
│  │                                      ↓             │   │
│  │                     Audio stream → Lead's phone     │   │
│  └──────────────────────────────────────────────────┘   │
│                                                           │
│  Webhooks fired to our Voice Service:                     │
│  ├── call_started  → Lead marked 'contacted'             │
│  ├── call_ended    → Transcript saved to MongoDB         │
│  └── call_analyzed → Triggers Agent B evaluation         │
└─────────────────────────────────────────────────────────┘
```

### Dynamic Variables injected at call time:
| Variable | Description |
|---|---|
| `{{lead_first_name}}` | Lead's first name for personalization |
| `{{lead_company}}` | Company for B2B context |
| `{{lead_source}}` | Where lead came from (Facebook, Google, etc.) |
| `{{lead_id}}` | Internal ID for tracking |
| `{{tenant_id}}` | Business identifier for multi-tenant |
| `{{call_timestamp}}` | Current date/time for the agent |

---

## 3. Self-Improving Feedback Loop

```
New Lead Created
      │
      ▼
Voice Service → Retell API: create_phone_call()
      │
      ▼
Retell AI conducts conversation (STT + LLM + TTS)
      │
      ▼
call_analyzed webhook → conversation.completed Kafka event
      │
      ▼
Agent B Service consumes event
      │
      ├──► TranscriptAnalyzer.analyze()
      │         Uses GPT-4o to evaluate:
      │         ✓ Factual accuracy vs knowledge base
      │         ✓ Qualification questions asked
      │         ✓ Missed upselling opportunities
      │         ✓ Objection handling quality
      │         ✓ FAQ answer correctness
      │
      ├──► PerformanceEvaluator → EvaluationReport saved to MongoDB
      │
      ├──► LearningGenerator.generate_learnings()
      │         For each critical/high mistake:
      │         → Generate correction_prompt_snippet
      │         → Create Learning record
      │
      ├──► KnowledgeUpdater (if successful call)
      │         → Add Q&A pairs to Qdrant as successful examples
      │
      └──► Retell Agent Update (high confidence learnings)
                retell.update_agent({
                  general_prompt: "... + correction snippet"
                })
                → Future calls immediately improved
```

### Rollback Mechanism:
- Every Retell agent update stores the **previous prompt version** in MongoDB
- If evaluation scores drop after an update: `POST /api/v1/learnings/{id}/rollback`
- Reverts to the previous prompt version via `retell.update_agent()`
- Learning status set to `rolled_back` with reason

---

## 4. Database Architecture

### PostgreSQL (Structured relational data)
**Why**: ACID transactions, foreign keys, complex queries for billing/RBAC/leads

| Table | Purpose |
|---|---|
| `tenants` | Multi-tenant isolation root |
| `users` | Authentication, RBAC |
| `leads` | Lead pipeline, status tracking |
| `lead_status_history` | Full audit trail of status changes |
| `meetings` | Calendar bookings |
| `agent_configurations` | Retell agent IDs per tenant |
| `subscriptions` | SaaS billing |
| `audit_logs` | Security audit trail |

### MongoDB (Document store for AI data)
**Why**: Schema-flexible, ideal for conversations with variable structure

| Collection | Purpose |
|---|---|
| `calls` | Full call records with transcripts, analysis |
| `evaluation_reports` | Agent B evaluations per call |
| `learnings` | Generated improvements with status |
| `agent_memory` | Per-tenant AI context |
| `prompt_versions` | Versioned prompt history |

### Redis (Cache + Pub/Sub)
**Why**: Sub-millisecond access for hot paths

| Key Pattern | Purpose |
|---|---|
| `retell:active_call:{lead_id}` | Prevent duplicate calls |
| `retell:lock:{lead_id}` | Distributed call lock (30s TTL) |
| `retell:attempts:{lead_id}` | Retry counter per lead |
| `session:{user_id}` | Auth session cache |
| `rate_limit:{tenant_id}:{endpoint}` | API rate limiting |
| `dashboard:updates:{tenant_id}` | Pub/Sub for real-time dashboard |

### Qdrant Vector DB (Knowledge + RAG)
**Why**: High-performance vector similarity search for knowledge retrieval

| Collection | Purpose |
|---|---|
| `tenant_{id}_knowledge` | Company FAQs, pricing, products |
| `tenant_{id}_conversations` | Successful conversation embeddings |

---

## 5. Kafka Event Flow

```
TOPIC              PUBLISHER          CONSUMER(S)
─────              ─────────          ────────────
lead.created       lead-service       voice-service (triggers Retell call)
                                      agent-a-service (chat channels)
                                      analytics-service

call.initiated     voice-service      analytics-service, dashboard

call.started       voice-service      lead-service (status → 'contacted')
                   (Retell webhook)   analytics-service

call.ended         voice-service      lead-service (status update)
                   (Retell webhook)   analytics-service

conversation.      voice-service      agent-b-service (EVALUATION)
completed          (Retell analyzed)  analytics-service
                                      notification-service

evaluation.        agent-b-service    analytics-service
completed                             notification-service

learning.applied   agent-b-service    notification-service
                                      analytics-service

meeting.booked     agent-a-service    calendar-service
                                      lead-service (status → 'meeting_booked')
                                      analytics-service

dashboard.update   analytics-service  notification-service (WebSocket push)
```

---

## 6. API Structure

### Auth Service (`/api/v1/auth`)
| Method | Endpoint | Description |
|---|---|---|
| POST | `/register` | Register tenant + user |
| POST | `/login` | Get JWT tokens |
| POST | `/refresh` | Refresh access token |
| POST | `/logout` | Invalidate refresh token |
| GET | `/me` | Current user profile |
| POST | `/oauth/google` | Google OAuth2 |

### Lead Service (`/api/v1/leads`)
| Method | Endpoint | Description |
|---|---|---|
| POST | `/` | Create lead → publishes `lead.created` |
| GET | `/` | List leads with filters |
| GET | `/{id}` | Get lead |
| PATCH | `/{id}/status` | Update status |
| GET | `/{id}/history` | Status audit trail |
| POST | `/bulk` | Bulk import |
| GET | `/stats/summary` | Lead counts by status |

### Webhooks (`/api/v1/webhooks`)
| Method | Endpoint | Description |
|---|---|---|
| GET/POST | `/facebook` | Facebook Lead Ads |
| POST | `/google` | Google Ads Lead Forms |
| POST | `/linkedin` | LinkedIn Lead Gen |
| POST | `/crm/{type}` | HubSpot / Salesforce / Zoho |
| POST | `/website-form` | Website contact form |
| POST | `/whatsapp` | WhatsApp messages |

### Voice Service (`/api/v1`)
| Method | Endpoint | Description |
|---|---|---|
| POST | `/calls/initiate` | Manual call trigger |
| POST | `/calls/web-call` | WebRTC browser call |
| GET | `/calls/{id}` | Get call + transcript |
| DELETE | `/calls/{id}/end` | End active call |
| POST | `/webhooks/retell` | **Retell AI webhook receiver** |
| GET | `/phone-numbers/` | List Retell phone numbers |
| POST | `/phone-numbers/import` | Register Twilio number |

### Agent B (`/api/v1`)
| Method | Endpoint | Description |
|---|---|---|
| GET | `/evaluations/` | List evaluations |
| GET | `/evaluations/{id}` | Full evaluation report |
| GET | `/evaluations/mistakes` | All detected mistakes |
| GET | `/learnings/` | List learnings |
| POST | `/learnings/{id}/apply` | Apply learning |
| POST | `/learnings/{id}/rollback` | Rollback learning |
| GET | `/learnings/timeline` | Agent evolution timeline |
| GET | `/reports/business-performance` | Owner dashboard |

---

## 7. AWS Deployment Architecture

```
                         Route 53 (DNS)
                               │
                    ┌──────────▼──────────┐
                    │   CloudFront CDN     │
                    │   (Static Assets)    │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │   ALB (Load Balancer)│
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │      EKS Cluster     │
                    │  ┌────────────────┐  │
                    │  │ api-gateway    │  │  3 replicas
                    │  │ auth-service   │  │  2 replicas
                    │  │ lead-service   │  │  4 replicas
                    │  │ agent-a-service│  │  2 replicas
                    │  │ agent-b-service│  │  2 replicas
                    │  │ voice-service  │  │  4 replicas (handles Retell webhooks)
                    │  │ analytics      │  │  2 replicas
                    │  └────────────────┘  │
                    └──────────┬──────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                     │
 ┌────────▼──────┐    ┌────────▼──────┐    ┌───────▼──────┐
 │  RDS Aurora   │    │  ElastiCache  │    │    MSK       │
 │  PostgreSQL   │    │  Redis        │    │   (Kafka)    │
 │  Multi-AZ     │    │  Cluster Mode │    │  3 brokers   │
 └───────────────┘    └───────────────┘    └─────────────┘
          │
 ┌────────▼──────┐    ┌───────────────┐
 │  DocumentDB   │    │      S3       │
 │  (MongoDB)    │    │  Recordings   │
 │  3 nodes      │    │  Transcripts  │
 └───────────────┘    └───────────────┘
```

### Cost Estimation (100K leads/day)
| Service | Instance | Monthly Cost |
|---|---|---|
| EKS Cluster (10 t3.xlarge nodes) | On-demand | ~$1,500 |
| RDS Aurora PostgreSQL (r6g.large) | Multi-AZ | ~$300 |
| DocumentDB (r6g.large, 3 nodes) | - | ~$500 |
| ElastiCache Redis (r6g.large) | Cluster | ~$250 |
| MSK Kafka (kafka.m5.large, 3) | - | ~$400 |
| ALB | - | ~$50 |
| CloudFront | 1TB/month | ~$85 |
| S3 (recordings) | 5TB | ~$120 |
| Retell AI | ~60s avg × 100K calls | ~$3,000–6,000 |
| **Total** | | **~$6,200–9,200/mo** |

---

## 8. Scaling Strategy (1M leads/month)

### Horizontal Scaling
- **Voice Service**: Scale to 20+ pods — each handles Retell webhooks independently
- **Lead Service**: Kafka partitions = pod count (auto-scale on queue depth)
- **Agent B**: Scale evaluations with Kafka consumer groups

### Retell AI Concurrency
- Retell supports **unlimited concurrent calls** on enterprise plan
- Our Redis lock prevents duplicate calls per lead
- Target: < 60 seconds from lead creation to Retell call connected

### Database Scaling
- PostgreSQL: Read replicas for analytics queries
- MongoDB: Sharding on `tenant_id`
- Redis: Cluster mode with 3 shards
- Qdrant: Distributed mode with replication factor 2

---

## 9. Security Best Practices

1. **Retell Webhook Verification**: HMAC-SHA256 signature on every webhook
2. **JWT Short-lived Tokens**: 60-minute access tokens, 7-day refresh
3. **Multi-tenant Isolation**: Every query scoped to `tenant_id`
4. **Secrets Management**: AWS Secrets Manager for all API keys
5. **Phone Number Masking**: Lead phone numbers encrypted at rest
6. **RBAC Enforcement**: 4-tier role system on every endpoint
7. **Rate Limiting**: Per-tenant API limits via Redis
8. **Audit Logging**: Every data change tracked in `audit_logs`
9. **TLS Everywhere**: All inter-service communication encrypted
10. **Retell Agent Keys**: Per-tenant Retell agents for data isolation

---

## 10. Monitoring & Observability

### Key Metrics to Track
| Metric | Alert Threshold |
|---|---|
| Time-to-contact (lead created → call started) | > 90 seconds |
| Retell call success rate | < 85% |
| Agent B evaluation backlog | > 100 pending |
| API gateway p99 latency | > 500ms |
| Kafka consumer lag | > 1000 messages |
| Agent overall score (Agent B) | < 6.0/10 |
| Learning application failure rate | > 10% |

### Dashboards
- **Lead Pipeline**: Real-time funnel visualization
- **Call Performance**: Duration, success rate, sentiment trends
- **Agent Evolution**: Score improvements over time
- **Business KPIs**: Conversion rate, cost-per-meeting, ROI
