# ShadowTrade v1

A high-reliability **Copy Trading Engine** for the Indian equity markets (NSE/BSE). ShadowTrade replicates all trading activity from a master account to multiple client (follower) accounts in near real-time, with strict safety controls and full auditability.

Built for **swing trading** — where positional accuracy matters more than microsecond latency.

---

## System Guarantees

- Client positions always equal master positions
- Zero duplicate orders across any client account
- No execution beyond 2% price deviation from master
- No missed exits (stop-loss, targets, partial/full)
- No capital overexposure on any client account

## Architecture

```
Master Account (Broker)
        │
        ▼
┌──────────────────────────┐
│    ShadowTrade Engine    │
│                          │
│  ┌────────────────────┐  │
│  │ Master Detector    │──│── WebSocket + REST polling
│  │ (dual-channel)     │  │
│  └────────┬───────────┘  │
│           ▼              │
│  ┌────────────────────┐  │
│  │ Event Store (PG)   │  │── Persist-before-process
│  └────────┬───────────┘  │
│           ▼              │
│  ┌────────────────────┐  │
│  │ Distribution       │  │── Slippage guard, capital check,
│  │ Orchestrator       │  │   idempotency, partial fills
│  └────────┬───────────┘  │
│           ▼              │
│  ┌────────────────────┐  │
│  │ Execution Engine   │  │── Per-client rate limiters (true parallel)
│  └────────────────────┘  │
│                          │
│  Reconciliation Engine   │── Every 60s position verification
│  Kill Switch             │── Emergency halt (API-controlled)
│  Audit Logger            │── Immutable append-only trail
└──────────────────────────┘
        │
        ▼
Client Accounts (30+)
```

## Tech Stack

| Layer          | Technology                          |
|----------------|-------------------------------------|
| Language       | Python 3.11+                        |
| Framework      | FastAPI + asyncio + uvloop          |
| Database       | PostgreSQL 16 (asyncpg)             |
| Cache          | Redis 7.2                           |
| Broker         | Zerodha Kite Connect (postback webhooks)     |
| Monitoring     | Prometheus + Grafana                         |
| Deployment     | Docker on AWS Mumbai (t4g.medium burstable ARM) |

## Core Features (14 FRs)

| #  | Feature                          | Status  |
|----|----------------------------------|---------|
| 01 | Master Trade Detection (WS+REST) | Planned |
| 02 | Persistent Trade Event Store     | Planned |
| 03 | Idempotent Execution Layer       | Planned |
| 04 | Parallel Order Execution Engine  | Planned |
| 05 | 2% Slippage Protection           | Planned |
| 06 | Partial Fill Handling            | Planned |
| 07 | Order Modification & Cancel Sync | Planned |
| 08 | Exit Synchronization Engine      | Planned |
| 09 | Position Reconciliation Engine   | Planned |
| 10 | Client Capital Protection        | Planned |
| 11 | Safe Retry Logic                 | Planned |
| 12 | Kill Switch (System Safety)      | Planned |
| 13 | Immutable Audit Trail            | Planned |
| 14 | Restart Recovery Mechanism       | Planned |

## Documentation

| Document | Description |
|----------|-------------|
| [BRD](docs/BRD.md) | Business Requirements Document — functional requirements, acceptance criteria, risks |
| [HLD](docs/HLD.md) | High-Level Design — architecture, components, data model, interaction flows |
| [LLD](docs/LLD.md) | Low-Level Design — code, API details, broker comparison, hosting, implementation roadmap |

## Quick Start

```bash
# Clone
git clone https://github.com/raghusodani/shadowtrade-v1.git
cd shadowtrade-v1

# Configure
cp .env.example .env
# Edit .env with your broker API keys

# Run
docker compose up -d

# Health check
curl http://localhost:8000/api/v1/health
```

## Project Structure

```
shadowtrade-v1/
├── docs/                  # BRD, HLD, LLD documentation
│   ├── BRD.md
│   ├── HLD.md
│   └── LLD.md
├── config/                # Pydantic settings
├── app/
│   ├── main.py            # FastAPI entry point
│   ├── auth/              # Per-client OAuth token management
│   ├── broker/            # Broker adapter layer (Zerodha, per-client API keys)
│   ├── core/              # Business logic (14 FR modules)
│   ├── api/               # REST management endpoints
│   ├── models/            # SQLAlchemy ORM models
│   ├── schemas/           # Pydantic request/response schemas
│   └── utils/             # Rate limiter, metrics, exceptions
├── tests/                 # Unit + integration tests
├── alembic/               # Database migrations
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Cost

| Configuration (30 clients)                  | Monthly Cost  | Execution Time |
|---------------------------------------------|---------------|----------------|
| Infra + 1 shared API key                    | ~₹10,200      | ~3 sec         |
| Infra + 5 keys (balanced)                   | ~₹12,700      | ~1.2 sec       |
| Infra + 10 keys (performance)               | ~₹15,200      | ~0.6 sec       |
| Infra + 30 keys (recommended)               | ~₹25,200      | <0.5 sec       |

At ₹500/key, 30 keys adds only ₹15,000/month (~₹500/client) for true parallel execution and full fault isolation. See [LLD Section 2.1.1](docs/LLD.md#211-api-key-strategy-analysis-30-clients) for the full trade-off analysis.

## Timeline

8-week implementation plan across 5 phases — see [LLD](docs/LLD.md#14-implementation-roadmap) for details.

## License

Private — All rights reserved.
