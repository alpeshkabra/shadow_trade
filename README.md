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

A runnable **reference implementation** of the engine lives under
[`src/shadow_trade/`](src/shadow_trade). It ships with an in-memory
**simulated broker**, so you can watch the full
`copy → slippage guard → idempotency → reconcile` flow end-to-end without a
real broker or a PostgreSQL instance.

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the end-to-end demo:
#   master trades -> copied to 3 followers -> 2% slippage guard -> reconcile
python run_demo.py        # or: make demo

# Run the test suite (slippage, idempotency, orchestration, reconciliation)
pytest                    # or: make test

# Launch the management API (HLD section 12) and open http://localhost:8000/docs
make api                  # uvicorn shadow_trade.asgi:app
curl http://localhost:8000/api/v1/health
```

By default the engine persists to a local SQLite database; point
`DATABASE_URL` at PostgreSQL for production (see [`.env.example`](.env.example)).

## Project Structure

```
shadow_trade/
├── docs/                       # BRD, HLD, LLD documentation
│   ├── BRD.md
│   ├── HLD.md
│   └── LLD.md
├── src/shadow_trade/
│   ├── engine.py               # CopyTradingEngine — composition root
│   ├── detector.py             # Master Trade Detector (HLD 4.1)
│   ├── orchestrator.py         # Distribution + Parallel Execution (HLD 4.3/4.4)
│   ├── slippage.py             # Slippage Guard — 2% deviation (HLD 4.3)
│   ├── capital.py              # Capital / margin validator (HLD 4.3)
│   ├── reconciliation.py       # Reconciliation Engine (HLD 4.5)
│   ├── kill_switch.py          # Kill Switch Controller (HLD 4.6)
│   ├── audit.py                # Append-only Audit Logger (HLD 4.7)
│   ├── models.py               # SQLAlchemy ORM models (HLD section 5)
│   ├── api.py                  # FastAPI management endpoints (HLD section 12)
│   ├── asgi.py                 # ASGI entrypoint (uvicorn shadow_trade.asgi:app)
│   └── brokers/                # Broker abstraction + SimulatedBroker
├── tests/                      # pytest suite
├── run_demo.py                 # end-to-end demo
├── docker-compose.yml          # engine + PostgreSQL
├── Dockerfile
├── Makefile                    # install / demo / test / api
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
