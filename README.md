# 🚀 AI Lead Engagement Platform

> Production-grade, multi-tenant SaaS backend for AI-powered lead engagement.
> Contact leads within seconds of creation. Scale to 100K+ leads/day.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        API Gateway (NestJS)                      │
│              JWT Auth │ Rate Limiting │ Tenant Routing           │
└──────────┬──────────────────────────────────┬────────────────────┘
           │ REST/HTTP                         │ gRPC
    ┌──────▼──────┐                    ┌───────▼────────┐
    │ Auth Service│                    │  Lead Service  │
    │  JWT/OAuth2 │                    │  (FastAPI)     │
    │    RBAC     │                    └───────┬────────┘
    └─────────────┘                            │ Kafka: lead.created
                                       ┌───────▼────────┐
                                       │ Agent A Service │
                                       │  (FastAPI+LLM) │
                                       └───────┬────────┘
                                               │ Kafka: conversation.completed
                                       ┌───────▼────────┐
                                       │ Agent B Service │
                                       │  (FastAPI+LLM) │
                                       └───────┬────────┘
                                               │
                                    ┌──────────▼──────────┐
                                    │   Analytics Service  │
                                    │   Dashboard Updates  │
                                    └─────────────────────┘
```

## Services

| Service | Framework | Port | Purpose |
|---------|-----------|------|---------|
| api-gateway | NestJS | 3000 | Public API entry point |
| auth-service | FastAPI | 8001 | JWT, OAuth2, RBAC |
| lead-service | FastAPI | 8002 | Lead management |
| agent-a-service | FastAPI | 8003 | Lead engagement AI |
| agent-b-service | FastAPI | 8004 | AI Manager/evaluator |
| voice-service | FastAPI | 8005 | Voice calls (Twilio/ElevenLabs) |
| calendar-service | FastAPI | 8006 | Calendar integrations |
| analytics-service | FastAPI | 8007 | Real-time analytics |
| notification-service | NestJS | 3001 | WebSocket dashboard |

## Quick Start

```bash
# Clone and start all services
docker-compose up -d

# Check health
curl http://localhost:3000/health
```

## Tech Stack

- **API**: FastAPI (Python), NestJS (Node.js)
- **Internal Comms**: gRPC
- **Events**: Apache Kafka
- **DB**: PostgreSQL + MongoDB + Redis
- **Vector DB**: Qdrant
- **Voice**: Twilio + Deepgram + ElevenLabs + OpenAI Realtime
- **AI**: LangChain + OpenAI GPT-4o
- **Infra**: Docker + Kubernetes + Terraform + AWS

## Documentation

- [Architecture Diagrams](./docs/architecture.md)
- [API Reference](./docs/api-reference.md)
- [Database Schema](./docs/database-schema.md)
- [Deployment Guide](./docs/deployment.md)
