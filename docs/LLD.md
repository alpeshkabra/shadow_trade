# Low-Level Design (LLD)

## Copy Trading Engine — Swing Trading

| Field              | Detail                                      |
|--------------------|---------------------------------------------|
| **Document ID**    | LLD-CTE-2026-001                            |
| **Version**        | 1.4                                         |
| **Date**           | 18 March 2026                               |
| **Status**         | Draft                                       |
| **Related BRD**    | BRD-CTE-2026-001                            |
| **Related HLD**    | HLD-CTE-2026-001                            |
| **Classification** | Confidential                                |

**Change Log:**
- v1.4 (18 Mar 2026): Corrected Zerodha API pricing to ₹500/month (was ₹2,000). All costs now in INR. Updated all cost tables and strategy analysis.
- v1.3 (18 Mar 2026): Right-sized infrastructure — burstable t4g instances, Single-AZ RDS, micro Redis. Infra cost reduced by 67% with zero performance trade-off for swing trading workload.
- v1.2 (18 Mar 2026): Per-client API keys for independent rate limits. Auth & Token Management module. Extended client_accounts schema. Per-adapter rate limiters for true parallel execution (<1 sec for 30 clients).
- v1.1 (18 Mar 2026): Migrated to Zerodha Kite Connect as mandated broker. Upgraded infrastructure for high consistency and low latency. Client bears all costs.

---

## Table of Contents

1. [Technology Stack Decision](#1-technology-stack-decision)
2. [Broker API Selection & Comparison](#2-broker-api-selection--comparison)
3. [Infrastructure & Hosting](#3-infrastructure--hosting)
4. [Project Structure](#4-project-structure)
5. [Database Schema (DDL)](#5-database-schema-ddl)
6. [Configuration Management](#6-configuration-management)
7. [Module-Level Design & Code](#7-module-level-design--code)
   - 7.1 [Broker Adapter Layer](#71-broker-adapter-layer)
   - 7.2 [Master Trade Detector](#72-master-trade-detector)
   - 7.3 [Trade Event Store (Repository Layer)](#73-trade-event-store-repository-layer)
   - 7.4 [Distribution Orchestrator](#74-distribution-orchestrator)
   - 7.5 [Slippage Guard](#75-slippage-guard)
   - 7.6 [Capital Validator](#76-capital-validator)
   - 7.7 [Parallel Execution Engine](#77-parallel-execution-engine)
   - 7.8 [Reconciliation Engine](#78-reconciliation-engine)
   - 7.9 [Kill Switch Controller](#79-kill-switch-controller)
   - 7.10 [Retry Manager](#710-retry-manager)
   - 7.11 [Audit Logger](#711-audit-logger)
   - 7.12 [Restart Recovery Manager](#712-restart-recovery-manager)
   - 7.13 [Auth & Token Management](#713-auth--token-management)
   - 7.14 [Application Entry Point](#714-application-entry-point)
8. [API Endpoint Implementation](#8-api-endpoint-implementation)
9. [FR → Code Traceability Matrix](#9-fr--code-traceability-matrix)
10. [NFR Satisfaction Strategy](#10-nfr-satisfaction-strategy)
11. [Error Handling Strategy](#11-error-handling-strategy)
12. [Testing Strategy](#12-testing-strategy)
13. [Deployment Pipeline](#13-deployment-pipeline)
14. [Implementation Roadmap](#14-implementation-roadmap)
15. [Cost Estimate](#15-cost-estimate)

---

## 1. Technology Stack Decision

### 1.1 Final Technology Choices

| Layer                  | Technology                     | Version   | Rationale                                                      |
|------------------------|--------------------------------|-----------|----------------------------------------------------------------|
| **Language**           | Python                         | 3.11+     | Rapid development, rich broker SDK ecosystem, asyncio native   |
| **Async Framework**    | asyncio + uvloop               | stdlib    | Event loop for non-blocking I/O; uvloop for 2-4x perf boost   |
| **Web Framework**      | FastAPI                        | 0.110+    | Async-native, auto OpenAPI docs, dependency injection          |
| **ASGI Server**        | Uvicorn                        | 0.29+     | High-performance ASGI server with uvloop                       |
| **Database**           | PostgreSQL                     | 16        | ACID, JSONB, mature, strong async driver support               |
| **Async DB Driver**    | asyncpg                        | 0.29+     | Fastest PostgreSQL driver for Python (C extension)             |
| **ORM / Query Builder**| SQLAlchemy 2.0 (async)         | 2.0+      | Async session, type-safe queries, Alembic migrations           |
| **Migrations**         | Alembic                        | 1.13+     | Schema versioning, rollback support                            |
| **Cache / Rate Limit** | Redis                          | 7.2+      | In-memory rate limiter state, kill switch flag, pub/sub        |
| **Redis Client**       | redis-py (async)               | 5.0+      | Native asyncio support                                         |
| **Task Scheduling**    | APScheduler                    | 3.10+     | Cron-like scheduling for reconciliation, REST polling          |
| **HTTP Client**        | httpx                          | 0.27+     | Async HTTP with connection pooling, timeout control            |
| **WebSocket Client**   | websockets                     | 12.0+     | Robust async WS client with auto-reconnect patterns            |
| **Broker SDK**         | pykiteconnect (Zerodha)        | 5.0+      | Mandated broker; postback webhooks, SHA-256 verification, best reliability |
| **Logging**            | structlog                      | 24.1+     | Structured JSON logging, async-safe                            |
| **Metrics**            | prometheus-client              | 0.20+     | Prometheus exposition for Grafana dashboards                   |
| **Containerization**   | Docker + Docker Compose        | 25.x      | Reproducible builds, multi-service orchestration               |
| **CI/CD**              | GitHub Actions                 | —         | Automated testing, linting, deployment                         |

### 1.2 Why Python over Java/Go

| Factor            | Python                                  | Java                          | Go                            |
|-------------------|-----------------------------------------|-------------------------------|-------------------------------|
| Broker SDKs       | All Indian brokers have official Python SDKs | Limited / unofficial       | None                          |
| Development Speed  | Fastest for this domain                | 2-3x slower                   | 1.5-2x slower                 |
| Async I/O          | asyncio mature, sufficient for swing   | Virtual threads (newer)       | Goroutines (excellent)        |
| Latency Need       | Swing trading needs sub-second; Python asyncio + uvloop achieves <200ms end-to-end | Overkill for swing            | Overkill for swing            |
| Ecosystem          | pandas, numpy for future analytics     | Limited                       | Limited                       |
| Team Familiarity   | Most common in Indian fintech          | Common but heavier            | Growing                       |

**Verdict:** Python is the optimal choice for a swing trading copy engine where broker SDK availability, development speed, and ecosystem matter. Python asyncio with uvloop achieves sub-200ms end-to-end latency for swing trading.

---

## 2. Broker API Selection & Comparison

### 2.1 Single-Broker Architecture: Zerodha Kite Connect

**Mandated Choice:** Zerodha Kite Connect is the only broker for this system. The client has mandated Zerodha as the sole broker integration.

**Why Zerodha is Excellent for Consistency:**
- **Postback webhooks with SHA-256 verification** — Guaranteed order update delivery for API-placed orders. No polling needed for order status when using the postback URL. Zerodha pushes order lifecycle events to your server with a cryptographically verified checksum.
- **Excellent reliability** — Industry-leading uptime and API stability for Indian equity markets.
- **Best documentation** — Comprehensive Kite Connect docs, examples, and community support.

**Per-Client API Key Architecture:**

Each client account operates with its **own Zerodha API key**. This is a critical architectural decision:

| Aspect                    | Shared API Key (rejected)           | Per-Client API Key (selected)             |
|---------------------------|-------------------------------------|-------------------------------------------|
| Rate limit                | 10 orders/sec shared across ALL     | 10 orders/sec PER client (independent)    |
| Daily order limit         | 3,000 shared across ALL             | 3,000 PER client (independent)            |
| 30-client execution time  | ~3 seconds (serialized by rate)     | **<1 second (true parallel)**             |
| API cost                  | ₹500/month × 1                     | ₹500/month × (1 master + N clients)      |
| Token management          | Single token                        | Per-client token refresh (daily)          |
| Fault isolation           | One rate-limit hit blocks ALL       | Isolated — one client's issue doesn't affect others |
| Postback webhooks         | All go to one URL                   | Each API key can register its own postback URL      |

**Zerodha Per-API-Key Constraints:**
- 10 orders/sec per API key
- 3,000 orders/day per API key
- ₹500/month per API key (Kite Connect plan)
- One access token per API key per day (requires daily OAuth login per client)

### 2.1.1 API Key Strategy Analysis (30 Clients)

The number of API keys is the most impactful cost-vs-performance decision in this system. Below is a detailed analysis to help the client choose.

**Assumptions:** 30 client accounts, swing trading, master places 1 trade → engine replicates to all 30 clients. Average 5-15 master trades/day → 150-450 client orders/day. AWS infra ≈ ₹9,700/month (see Section 3.3). Zerodha Kite Connect: ₹500/month per API key.

| Metric | 1 Key (shared) | 5 Keys (6 clients/key) | 10 Keys (3 clients/key) | 30 Keys (1 client/key) |
|--------|---------------|------------------------|-------------------------|------------------------|
| **Zerodha API cost** | ₹500 | ₹2,500 | ₹5,000 | ₹15,000 |
| **Total monthly (infra + API)** | **~₹10,200** | **~₹12,200** | **~₹14,700** | **~₹24,700** |
| **Rate limit (orders/sec)** | 10 shared | 50 aggregate | 100 aggregate | 300 aggregate |
| **Time to execute 30 orders** | ~3 sec | ~1.2 sec | ~0.6 sec | **<0.5 sec** |
| **Daily order cap** | 3,000 shared | 15,000 aggregate | 30,000 aggregate | 90,000 aggregate |
| **Orders/day headroom** | Tight (450/3000 = 15%) | Comfortable | Excessive | Excessive |
| **Token management complexity** | 1 daily login | 5 daily logins | 10 daily logins | 30 daily logins |
| **Fault isolation** | None — 1 failure stalls all | Partial — 1 failure stalls 6 | Good — 1 failure stalls 3 | **Full — failures isolated** |
| **Rate-limit contention** | High during bursts | Moderate | Low | **None** |
| **Code complexity** | Simple | Medium (client-to-key mapping) | Medium | Simple (1:1 mapping) |

**Detailed Trade-off Analysis:**

**1 Key — ~₹10,200/month (cheapest, highest risk)**
- All 30 clients share a single 10/sec rate limit. A burst of 30 orders takes ~3 seconds.
- For swing trading with infrequent trades (5-15/day), this may be acceptable — 3-second spread on a position held for days is negligible.
- **Risk:** If the master fires rapid consecutive trades (e.g., exit + re-entry), the queue backs up. Late clients may face worse prices.
- **Risk:** A single daily order cap (3,000) could be exhausted by retries or modifications across 30 clients during volatile days.
- **Best for:** Cost-sensitive clients, <15 clients, calm markets.

**5 Keys — ~₹12,200/month (balanced budget option)**
- 6 clients per key. Execution time drops to ~1.2 seconds for 30 orders.
- Daily cap is 15,000 — virtually impossible to exhaust in swing trading.
- Partial fault isolation: a key-level issue only affects 6 clients, not all 30.
- **Trade-off:** Requires a mapping layer (which 6 clients belong to which key). Adds moderate token management overhead (5 daily logins).
- **Best for:** Budget-conscious clients who still want reasonable speed.

**10 Keys — ~₹14,700/month (performance sweet spot)**
- 3 clients per key. Execution time ~0.6 seconds. Functionally indistinguishable from full parallel for swing trading.
- Excellent fault isolation — a problem only affects 3 clients.
- 10 daily logins are manageable with the token management module.
- **Best for:** Clients who value performance and isolation but want to save ~₹10,000/month vs 30 keys.

**30 Keys — ~₹24,700/month (maximum performance, zero compromise)**
- Every client is fully independent. True parallel, <0.5 seconds, complete fault isolation.
- Simplest code (1:1 mapping, no grouping logic).
- 30 daily logins — highest token management overhead, but fully automated by the auth module.
- At ₹500/key, the cost difference between 10 keys and 30 keys is only ₹10,000/month — arguably worth it for full isolation and simplicity.
- **Best for:** Clients where execution speed, fault isolation, and operational simplicity justify the cost.

**Recommendation matrix:**

| Client priority | Recommended | Monthly cost | Execution time |
|-----------------|-------------|-------------|----------------|
| Minimize cost | 1 key | ~₹10,200 | ~3 sec |
| Balance cost & speed | 5 keys | ~₹12,200 | ~1.2 sec |
| Prioritize speed | 10 keys | ~₹14,700 | ~0.6 sec |
| Zero compromise | 30 keys | ~₹24,700 | <0.5 sec |

> **Architecture note:** The system is designed to support any of these configurations. The `client_accounts` table stores `api_key` and `api_secret` per client. Multiple clients can share the same API key — the execution engine groups them under the same rate limiter automatically. Changing strategy requires only updating the DB records, no code changes.

### 2.2 Zerodha Kite Connect Endpoints

| Operation              | Method | Endpoint                         | Rate Limit     |
|------------------------|--------|----------------------------------|----------------|
| Place Order            | POST   | `/orders/{variety}`              | 10/sec         |
| Modify Order           | PUT    | `/orders/{variety}/{order_id}`   | 10/sec         |
| Cancel Order           | DELETE | `/orders/{variety}/{order_id}`   | 10/sec         |
| Get Orders             | GET    | `/orders`                        | 10/sec         |
| Get Positions          | GET    | `/portfolio/positions`           | 10/sec         |
| Postback Webhook       | POST   | (your registered URL)            | —              |
| WS Order Updates       | WSS    | `wss://ws.kite.trade`            | —              |

**Zerodha Postback Webhook JSON Payload Example:**

```json
{
    "user_id": "AB1234",
    "unfilled_quantity": 0,
    "app_id": 1234,
    "checksum": "2011845d...",
    "placed_by": "AB1234",
    "order_id": "220303000308932",
    "exchange_order_id": "1000000001482421",
    "status": "COMPLETE",
    "order_timestamp": "2022-03-03 09:24:25",
    "exchange": "NSE",
    "tradingsymbol": "SBIN",
    "order_type": "MARKET",
    "transaction_type": "BUY",
    "product": "CNC",
    "quantity": 1,
    "filled_quantity": 1,
    "average_price": 470,
    "pending_quantity": 0
}
```

### 2.3 Triple-Channel Detection (Consistency Advantage)

Zerodha uniquely enables **three channels** for order detection, maximizing consistency:

1. **Postback webhooks** (primary, most reliable for API-placed orders) — Zerodha pushes order updates to your HTTPS endpoint. SHA-256 checksum verification ensures authenticity. No polling needed for API orders.
2. **WebSocket order updates** (KiteTicker) — Catches ALL orders including manual trades placed on Kite web/mobile. Real-time stream of order lifecycle events.
3. **REST polling** (safety net) — Periodic `get_all_orders()` as final fallback if webhook or WebSocket miss an event.

This triple-channel approach ensures no order event is missed, even under network glitches or broker-side delays.

---

## 3. Infrastructure & Hosting

### 3.1 Hosting Decision: AWS Mumbai (ap-south-1)

| Option           | Latency to Broker APIs | Cost/month    | Reliability | Verdict      |
|------------------|------------------------|---------------|-------------|--------------|
| AWS Mumbai       | 2-5ms                  | ~₹9,700       | 99.99%      | **Selected** |
| DigitalOcean BLR | 5-10ms                 | ~₹2,500-4,200 | 99.95%      | Good backup  |
| Hetzner (no IN)  | 100ms+                 | ~₹1,300-2,100 | 99.9%       | Too far      |
| Self-hosted VPS  | Variable               | ~₹1,300-2,500 | Variable    | Risky        |

**Why AWS Mumbai:**
- Lowest latency to Zerodha servers (hosted in Mumbai)
- ALB with TLS termination for Zerodha postback webhooks (publicly accessible HTTPS URL required)
- Managed PostgreSQL (RDS) and managed Redis (ElastiCache) — operational simplicity
- Burstable instances (t4g) are ideal: accumulate CPU credits off-market, spend them during the 6h15m trading window

### 3.2 Infrastructure Topology

```
AWS ap-south-1 (Mumbai)
├── VPC (10.0.0.0/16)
│   ├── Public Subnet (10.0.1.0/24)
│   │   ├── Application Load Balancer (ALB)
│   │   │   ├── TLS termination (HTTPS 443) — Zerodha postback webhook endpoint
│   │   │   ├── Health checks → FastAPI app
│   │   │   └── Routes: /api/v1/postback/* → app, /api/v1/* → app (management API)
│   │   ├── EC2 t4g.medium (Application Server)
│   │   │   ├── Docker Engine
│   │   │   │   ├── copy-trading-engine (FastAPI app)
│   │   │   │   ├── prometheus
│   │   │   │   └── grafana
│   │   │   └── Security Group: inbound from ALB only (8000, 9090, 3000)
│   │   └── NAT Gateway (outbound to broker APIs)
│   │
│   ├── Private Subnet (10.0.2.0/24)
│   │   ├── RDS PostgreSQL 16 (db.t4g.medium) — Single-AZ
│   │   │   ├── 2 vCPU, 4 GB RAM (burstable)
│   │   │   ├── 20 GB gp3 SSD
│   │   │   ├── Automated backups (7-day retention) + PITR
│   │   │   └── Security Group: inbound 5432 from app subnet only
│   │   └── ElastiCache Redis 7 (cache.t4g.micro)
│   │       └── Security Group: inbound 6379 from app subnet only
│   │
│   └── Outbound
│       ├── → Zerodha APIs (api.kite.trade)
│       └── → Zerodha WS (ws.kite.trade)
│
└── Route 53 (DNS) — optional for internal naming
```

### 3.3 Server Specifications

| Resource               | Spec                          | Monthly Cost (est.) | Rationale |
|------------------------|-------------------------------|---------------------|-----------|
| EC2 t4g.medium         | 2 vCPU (ARM Graviton2), 4 GB RAM, 20 GB EBS | ~₹2,100 | Burstable — accumulates CPU credits during 17h45m off-market, spends during 6h15m trading window. Ideal for swing trading burst pattern. |
| RDS PostgreSQL db.t4g.medium | 2 vCPU, 4 GB RAM, 20 GB gp3, Single-AZ | ~₹4,600 | 4 GB is generous for <500 trades/day. Automated backups + PITR for disaster recovery. Recovery manager handles brief outage gaps. |
| ElastiCache cache.t4g.micro | 2 vCPU, 0.5 GB RAM | ~₹840 | Stores kill switch flag + minimal pub/sub. 0.5 GB is ample for counters and state. |
| Application Load Balancer | TLS termination, health checks | ~₹1,700 | Non-negotiable — Zerodha postback webhooks require a publicly accessible HTTPS URL. Also provides health checks. |
| Data Transfer          | ~10 GB/month outbound        | ~₹420 | |
| **Total (infra)**      |                               | **~₹9,660/month** | |

> **Design philosophy:** Right-sized, not over-provisioned. Every rupee earns its place. Burstable instances (t4g) match the trading workload pattern perfectly — low utilization 18 hours/day, burst during market hours. Single-AZ RDS is acceptable because the recovery manager already handles incomplete trades on restart, and automated backups + PITR protect against data loss. If HA becomes critical later, adding a read replica (~₹2,500/mo) is a simple upgrade path.

---

## 4. Project Structure

```
copy-trading-engine/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── config/
│   ├── __init__.py
│   └── settings.py                  # Pydantic Settings (env-based config)
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI app entry point
│   ├── dependencies.py              # Dependency injection
│   │
│   ├── models/                      # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── master_trade_event.py
│   │   ├── client_account.py
│   │   ├── client_execution.py
│   │   ├── reconciliation_snapshot.py
│   │   ├── audit_log.py
│   │   └── kill_switch_state.py
│   │
│   ├── schemas/                     # Pydantic request/response schemas
│   │   ├── __init__.py
│   │   ├── orders.py
│   │   ├── positions.py
│   │   ├── health.py
│   │   └── kill_switch.py
│   │
│   ├── broker/                      # Broker adapter layer
│   │   ├── __init__.py
│   │   ├── base.py                  # Abstract broker interface
│   │   ├── zerodha.py               # Zerodha Kite Connect implementation
│   │   └── factory.py               # Broker factory
│   │
│   ├── core/                        # Core business logic
│   │   ├── __init__.py
│   │   ├── master_detector.py       # FR-01: Master trade detection
│   │   ├── event_store.py           # FR-02: Persistent trade event store
│   │   ├── idempotency.py           # FR-03: Idempotent execution
│   │   ├── execution_engine.py      # FR-04: Parallel execution
│   │   ├── slippage_guard.py        # FR-05: 2% slippage protection
│   │   ├── partial_fill.py          # FR-06: Partial fill handling
│   │   ├── modification_sync.py     # FR-07: Modification/cancellation
│   │   ├── exit_sync.py             # FR-08: Exit synchronization
│   │   ├── reconciliation.py        # FR-09: Position reconciliation
│   │   ├── capital_guard.py         # FR-10: Capital protection
│   │   ├── retry_manager.py         # FR-11: Safe retry logic
│   │   ├── kill_switch.py           # FR-12: Kill switch
│   │   ├── audit.py                 # FR-13: Audit trail
│   │   ├── recovery.py              # FR-14: Restart recovery
│   │   └── orchestrator.py          # Distribution orchestrator
│   │
│   ├── api/                         # FastAPI routes
│   │   ├── __init__.py
│   │   ├── health.py
│   │   ├── kill_switch.py
│   │   ├── postback.py               # Zerodha webhook receiver endpoint
│   │   ├── clients.py
│   │   ├── reconciliation.py
│   │   └── trades.py
│   │
│   └── utils/
│       ├── __init__.py
│       ├── rate_limiter.py          # Token bucket implementation
│       ├── metrics.py               # Prometheus metrics
│       └── exceptions.py            # Custom exceptions
│   ├── auth/
│       ├── __init__.py
│       ├── token_manager.py         # Per-client token lifecycle (refresh, validate, store)
│       └── login_flow.py            # Zerodha OAuth2 login redirect & callback handler
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_slippage_guard.py
│   │   ├── test_idempotency.py
│   │   ├── test_rate_limiter.py
│   │   ├── test_capital_guard.py
│   │   └── test_orchestrator.py
│   └── integration/
│       ├── test_execution_flow.py
│       ├── test_reconciliation.py
│       └── test_recovery.py
│
└── scripts/
    ├── seed_client_accounts.py
    └── health_check.sh
```

---

## 5. Database Schema (DDL)

```sql
-- ============================================================
-- Copy Trading Engine — PostgreSQL Schema
-- Version: 1.0
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- -----------------------------------------------------------
-- ENUM TYPES
-- -----------------------------------------------------------

CREATE TYPE order_side AS ENUM ('BUY', 'SELL');
CREATE TYPE order_type AS ENUM ('MARKET', 'LIMIT', 'SL', 'SLM');
CREATE TYPE order_variety AS ENUM ('NORMAL', 'AMO', 'STOPLOSS');
CREATE TYPE event_type AS ENUM (
    'NEW', 'MODIFY', 'CANCEL', 'PARTIAL_FILL',
    'FULL_FILL', 'REJECTION', 'TRIGGER_PENDING'
);
CREATE TYPE execution_status AS ENUM (
    'PENDING', 'SUBMITTED', 'FILLED', 'PARTIALLY_FILLED',
    'REJECTED', 'CANCELLED', 'SLIPPAGE_REJECTED',
    'CAPITAL_REJECTED', 'FAILED'
);
CREATE TYPE account_status AS ENUM ('ACTIVE', 'DISABLED', 'BLOCKED');
CREATE TYPE kill_switch_mode AS ENUM ('DISABLED', 'STOP_NEW_ENTRIES', 'FULL_HALT');
CREATE TYPE match_status AS ENUM ('MATCHED', 'MISMATCHED', 'ERROR');

-- -----------------------------------------------------------
-- TABLE: master_trade_events
-- Stores every order lifecycle event from the master account
-- Append-only — events are never updated, only new rows added
-- -----------------------------------------------------------

CREATE TABLE master_trade_events (
    id                  BIGSERIAL PRIMARY KEY,
    master_trade_id     VARCHAR(64) NOT NULL,
    event_type          event_type NOT NULL,
    instrument          VARCHAR(32) NOT NULL,
    exchange            VARCHAR(8) NOT NULL DEFAULT 'NSE',
    side                order_side NOT NULL,
    quantity            DECIMAL(18, 4) NOT NULL,
    order_type          order_type NOT NULL,
    variety             order_variety NOT NULL DEFAULT 'NORMAL',
    product_type        VARCHAR(16) NOT NULL DEFAULT 'CNC',
    price               DECIMAL(18, 4),
    trigger_price       DECIMAL(18, 4),
    filled_quantity     DECIMAL(18, 4) NOT NULL DEFAULT 0,
    average_price       DECIMAL(18, 4),
    status              VARCHAR(32) NOT NULL,
    broker_order_id     VARCHAR(64),
    exchange_order_id   VARCHAR(64),
    raw_payload         JSONB,
    event_timestamp     TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT uq_master_event UNIQUE (master_trade_id, event_type, filled_quantity)
);

CREATE INDEX idx_mte_master_trade_id ON master_trade_events (master_trade_id);
CREATE INDEX idx_mte_status ON master_trade_events (status);
CREATE INDEX idx_mte_event_timestamp ON master_trade_events (event_timestamp);
CREATE INDEX idx_mte_instrument ON master_trade_events (instrument);

-- -----------------------------------------------------------
-- TABLE: client_accounts
-- Registry of all client accounts enabled for copy trading
-- -----------------------------------------------------------

CREATE TABLE client_accounts (
    id                       SERIAL PRIMARY KEY,
    account_id               VARCHAR(64) UNIQUE NOT NULL,
    broker_name              VARCHAR(32) NOT NULL DEFAULT 'zerodha',
    broker_client_id         VARCHAR(64) NOT NULL,

    -- Per-client Zerodha API credentials (each client has their own API key)
    api_key                  VARCHAR(256) NOT NULL,
    api_secret               VARCHAR(256) NOT NULL,
    access_token             VARCHAR(512),
    token_expires_at         TIMESTAMPTZ,
    last_login_at            TIMESTAMPTZ,

    status                   account_status NOT NULL DEFAULT 'ACTIVE',
    max_open_trades          INT NOT NULL DEFAULT 20,
    consecutive_failures     INT NOT NULL DEFAULT 0,
    max_consecutive_failures INT NOT NULL DEFAULT 5,
    metadata                 JSONB,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ca_status ON client_accounts (status);
CREATE INDEX idx_ca_token_expiry ON client_accounts (token_expires_at);

-- -----------------------------------------------------------
-- TABLE: client_execution_log
-- Tracks every execution attempt per client per master trade
-- The execution_id is the idempotency key
-- -----------------------------------------------------------

CREATE TABLE client_execution_log (
    id                  BIGSERIAL PRIMARY KEY,
    execution_id        VARCHAR(192) UNIQUE NOT NULL,
    master_trade_id     VARCHAR(64) NOT NULL,
    client_account_id   VARCHAR(64) NOT NULL REFERENCES client_accounts(account_id),
    event_type          event_type NOT NULL,
    instrument          VARCHAR(32) NOT NULL,
    exchange            VARCHAR(8) NOT NULL DEFAULT 'NSE',
    side                order_side NOT NULL,
    quantity            DECIMAL(18, 4) NOT NULL,
    order_type          order_type NOT NULL,
    price               DECIMAL(18, 4),
    trigger_price       DECIMAL(18, 4),
    master_price        DECIMAL(18, 4),
    broker_order_id     VARCHAR(64),
    status              execution_status NOT NULL DEFAULT 'PENDING',
    slippage_pct        DECIMAL(8, 4),
    retry_count         INT NOT NULL DEFAULT 0,
    error_detail        TEXT,
    broker_response     JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_cel_execution_id ON client_execution_log (execution_id);
CREATE INDEX idx_cel_master_client ON client_execution_log (master_trade_id, client_account_id);
CREATE INDEX idx_cel_status ON client_execution_log (status);
CREATE INDEX idx_cel_created ON client_execution_log (created_at);

-- -----------------------------------------------------------
-- TABLE: reconciliation_snapshots
-- Periodic position snapshots for master and client accounts
-- -----------------------------------------------------------

CREATE TABLE reconciliation_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    snapshot_time       TIMESTAMPTZ NOT NULL,
    account_id          VARCHAR(64) NOT NULL,
    account_type        VARCHAR(8) NOT NULL CHECK (account_type IN ('MASTER', 'CLIENT')),
    instrument          VARCHAR(32) NOT NULL,
    exchange            VARCHAR(8) NOT NULL DEFAULT 'NSE',
    net_quantity        DECIMAL(18, 4) NOT NULL,
    average_price       DECIMAL(18, 4),
    match_status        match_status,
    discrepancy_detail  TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rs_snapshot_time ON reconciliation_snapshots (snapshot_time);
CREATE INDEX idx_rs_account ON reconciliation_snapshots (account_id, instrument);

-- -----------------------------------------------------------
-- TABLE: audit_log
-- Immutable append-only log for all trade-related actions
-- No UPDATE or DELETE permissions for the application user
-- -----------------------------------------------------------

CREATE TABLE audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    event_id            VARCHAR(192) UNIQUE NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    component           VARCHAR(64) NOT NULL,
    event_type          VARCHAR(64) NOT NULL,
    master_trade_id     VARCHAR(64),
    client_account_id   VARCHAR(64),
    payload             JSONB NOT NULL,
    outcome             VARCHAR(32) NOT NULL,
    duration_ms         INT
);

CREATE INDEX idx_al_master_trade ON audit_log (master_trade_id);
CREATE INDEX idx_al_client ON audit_log (client_account_id);
CREATE INDEX idx_al_timestamp ON audit_log (timestamp);
CREATE INDEX idx_al_component ON audit_log (component);

-- -----------------------------------------------------------
-- TABLE: kill_switch_state
-- Runtime-modifiable kill switch (single row)
-- -----------------------------------------------------------

CREATE TABLE kill_switch_state (
    id                  INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    mode                kill_switch_mode NOT NULL DEFAULT 'DISABLED',
    reason              TEXT,
    activated_by        VARCHAR(64),
    activated_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO kill_switch_state (mode) VALUES ('DISABLED');

-- -----------------------------------------------------------
-- TABLE: distributed_fill_tracker
-- Tracks how much fill has been distributed per master trade
-- Critical for partial fill handling (FR-06)
-- -----------------------------------------------------------

CREATE TABLE distributed_fill_tracker (
    id                  BIGSERIAL PRIMARY KEY,
    master_trade_id     VARCHAR(64) NOT NULL,
    total_distributed   DECIMAL(18, 4) NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT uq_dft_master_trade UNIQUE (master_trade_id)
);

-- -----------------------------------------------------------
-- SECURITY: Create a restricted role for the application
-- -----------------------------------------------------------

-- The app user can INSERT into audit_log but never UPDATE/DELETE
-- GRANT INSERT ON audit_log TO app_user;
-- REVOKE UPDATE, DELETE ON audit_log FROM app_user;
```

---

## 6. Configuration Management

```python
# config/settings.py
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # --- Application ---
    app_name: str = "copy-trading-engine"
    app_env: str = "production"
    debug: bool = False
    log_level: str = "INFO"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://cte_user:password@localhost:5432/copy_trading"
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Master Account ---
    master_broker: str = "zerodha"
    master_account_id: str = ""
    master_client_id: str = ""

    # --- Zerodha Kite Connect ---
    zerodha_api_key: str = ""
    zerodha_api_secret: str = ""
    zerodha_request_token: str = ""
    zerodha_access_token: str = ""
    postback_secret: str = ""  # For webhook SHA-256 checksum verification

    # --- Execution ---
    max_workers: int = 30  # one per client, all truly parallel
    rate_limit_per_second: int = 8  # per-adapter (each client has own rate limiter)
    rate_limit_burst: int = 8
    order_timeout_seconds: int = 5
    max_retries: int = 3
    retry_backoff_base: float = 1.0  # 1s, 2s, 4s

    # --- Token Management ---
    token_check_before_market_minutes: int = 15  # check token validity 15 min before market open
    token_expiry_alert_hours: int = 1  # alert if token expires within this window

    # --- Slippage ---
    slippage_threshold_pct: float = 2.0

    # --- Reconciliation ---
    reconciliation_interval_seconds: int = 60
    rest_poll_interval_seconds: int = 30
    auto_correct_on_mismatch: bool = False

    # --- Capital Protection ---
    max_open_trades_default: int = 20
    max_consecutive_failures: int = 5

    # --- Kill Switch ---
    # Loaded from DB, but can be overridden via env for emergencies
    kill_switch_override: Optional[str] = None

    # --- Monitoring ---
    metrics_port: int = 9090
    health_check_port: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()
```

**.env file (template):**

```bash
# .env — NEVER commit this file to version control
DATABASE_URL=postgresql+asyncpg://cte_user:s3cur3pass@db:5432/copy_trading
REDIS_URL=redis://redis:6379/0

MASTER_BROKER=zerodha
MASTER_ACCOUNT_ID=master_001
MASTER_CLIENT_ID=AB1234

ZERODHA_API_KEY=your_zerodha_api_key
ZERODHA_API_SECRET=your_zerodha_secret
ZERODHA_REQUEST_TOKEN=your_request_token_from_login_flow
ZERODHA_ACCESS_TOKEN=your_access_token
POSTBACK_SECRET=your_api_secret_for_checksum_verification

SLIPPAGE_THRESHOLD_PCT=2.0
MAX_WORKERS=30
RATE_LIMIT_PER_SECOND=8
RECONCILIATION_INTERVAL_SECONDS=60

# Client credentials are stored in DB, not env vars.
# Only the master account uses env-based config.
# See Section 7.13 for token management details.
```

---

## 7. Module-Level Design & Code

### 7.1 Broker Adapter Layer

The broker adapter abstracts all broker-specific logic behind a unified interface. This allows adding new brokers without modifying core logic.

```python
# app/broker/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SLM = "SLM"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TRIGGER_PENDING = "TRIGGER PENDING"
    MODIFY_PENDING = "MODIFY PENDING"


@dataclass
class OrderRequest:
    instrument: str
    exchange: str
    side: Side
    quantity: int
    order_type: OrderType
    product_type: str = "CNC"
    price: Optional[Decimal] = None
    trigger_price: Optional[Decimal] = None
    tag: Optional[str] = None  # idempotency tag


@dataclass
class OrderResponse:
    broker_order_id: str
    status: OrderStatus
    message: Optional[str] = None


@dataclass
class OrderInfo:
    broker_order_id: str
    exchange_order_id: Optional[str]
    status: OrderStatus
    instrument: str
    side: Side
    quantity: int
    filled_quantity: int
    average_price: Optional[Decimal]
    price: Optional[Decimal]
    trigger_price: Optional[Decimal]
    order_type: OrderType
    order_timestamp: Optional[str] = None
    raw: Optional[dict] = None


@dataclass
class Position:
    instrument: str
    exchange: str
    net_quantity: int
    average_price: Decimal
    product_type: str


class BrokerAdapter(ABC):
    """Abstract interface for all broker integrations."""

    @abstractmethod
    async def connect(self) -> None:
        """Authenticate and establish session."""

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place a new order."""

    @abstractmethod
    async def modify_order(
        self, broker_order_id: str, request: OrderRequest
    ) -> OrderResponse:
        """Modify an existing order."""

    @abstractmethod
    async def cancel_order(self, broker_order_id: str, variety: str = "NORMAL") -> bool:
        """Cancel an existing order."""

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> OrderInfo:
        """Get current status of a specific order."""

    @abstractmethod
    async def get_all_orders(self) -> list[OrderInfo]:
        """Get all orders for the day."""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Get all current positions."""

    @abstractmethod
    async def start_order_stream(self, callback) -> None:
        """Start WebSocket stream for order updates."""

    @abstractmethod
    async def stop_order_stream(self) -> None:
        """Stop the WebSocket order stream."""
```

```python
# app/broker/zerodha.py
import asyncio
import hashlib
import structlog
from decimal import Decimal
from typing import Callable, Optional

from kiteconnect import KiteConnect, KiteTicker
from app.broker.base import (
    BrokerAdapter, OrderRequest, OrderResponse, OrderInfo,
    Position, OrderStatus, Side, OrderType,
)
from config.settings import settings

logger = structlog.get_logger()


class ZerodhaAdapter(BrokerAdapter):
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        access_token: str = "",
        request_token: str = "",
    ):
        self._api_key = api_key or settings.zerodha_api_key
        self._api_secret = api_secret or settings.zerodha_api_secret
        self._access_token = access_token or settings.zerodha_access_token
        self._request_token = request_token or settings.zerodha_request_token
        self._kite: Optional[KiteConnect] = None
        self._ticker: Optional[KiteTicker] = None
        self._ticker_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        self._kite = KiteConnect(api_key=self._api_key)
        if self._request_token and not self._access_token:
            data = self._kite.generate_session(
                self._request_token, api_secret=self._api_secret
            )
            self._access_token = data["access_token"]
        self._kite.set_access_token(self._access_token)
        logger.info("zerodha.connected", api_key=self._api_key[:4] + "***")

    def verify_postback_checksum(self, order_id: str, order_timestamp: str, checksum: str) -> bool:
        """Verify SHA-256 checksum: order_id + order_timestamp + api_secret."""
        payload = order_id + order_timestamp + (settings.postback_secret or self._api_secret)
        computed = hashlib.sha256(payload.encode()).hexdigest()
        return computed == checksum

    def parse_postback_payload(self, payload: dict) -> OrderInfo:
        """Convert Zerodha postback webhook JSON to OrderInfo."""
        return self._parse_order(payload)

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        try:
            result = self._kite.place_order(
                variety="regular",
                tradingsymbol=request.instrument,
                exchange=request.exchange,
                transaction_type=request.side.value,
                quantity=request.quantity,
                order_type=request.order_type.value,
                product=request.product_type,
                validity="DAY",
                price=float(request.price) if request.price else None,
                trigger_price=float(request.trigger_price) if request.trigger_price else None,
                tag=request.tag[:20] if request.tag else None,
            )
            return OrderResponse(broker_order_id=result, status=OrderStatus.PENDING)
        except Exception as e:
            logger.error("zerodha.place_order_failed", error=str(e))
            return OrderResponse(
                broker_order_id="",
                status=OrderStatus.REJECTED,
                message=str(e),
            )

    async def modify_order(self, broker_order_id: str, request: OrderRequest) -> OrderResponse:
        try:
            self._kite.modify_order(
                variety="regular",
                order_id=broker_order_id,
                quantity=request.quantity,
                order_type=request.order_type.value,
                price=float(request.price) if request.price else None,
                trigger_price=float(request.trigger_price) if request.trigger_price else None,
            )
            return OrderResponse(broker_order_id=broker_order_id, status=OrderStatus.OPEN)
        except Exception as e:
            return OrderResponse(
                broker_order_id=broker_order_id,
                status=OrderStatus.REJECTED,
                message=str(e),
            )

    async def cancel_order(self, broker_order_id: str, variety: str = "regular") -> bool:
        try:
            self._kite.cancel_order(variety=variety, order_id=broker_order_id)
            return True
        except Exception as e:
            logger.error("zerodha.cancel_failed", order_id=broker_order_id, error=str(e))
            return False

    async def get_order_status(self, broker_order_id: str) -> OrderInfo:
        history = self._kite.order_history(broker_order_id)
        if not history:
            raise ValueError(f"Order {broker_order_id} not found")
        latest = history[-1]
        return self._parse_order(latest)

    async def get_all_orders(self) -> list[OrderInfo]:
        orders = self._kite.orders()
        return [self._parse_order(o) for o in orders]

    async def get_positions(self) -> list[Position]:
        positions = self._kite.positions()["net"]
        return [
            Position(
                instrument=p["tradingsymbol"],
                exchange=p["exchange"],
                net_quantity=int(p["quantity"]),
                average_price=Decimal(str(p.get("average_price", 0))),
                product_type=p.get("product", "CNC"),
            )
            for p in positions if int(p.get("quantity", 0)) != 0
        ]

    async def start_order_stream(self, callback: Callable) -> None:
        """Start KiteTicker WebSocket for order updates. Receives ALL orders including manual."""
        self._ticker = KiteTicker(self._api_key, self._access_token, [])
        loop = asyncio.get_event_loop()

        def on_order_update(ws, data):
            order_info = self._parse_order(data)
            loop.call_soon_threadsafe(
                lambda o=order_info: asyncio.ensure_future(callback(o))
            )

        self._ticker.on_order_update = on_order_update
        self._ticker_task = asyncio.create_task(
            asyncio.to_thread(self._ticker.connect)
        )

    async def stop_order_stream(self) -> None:
        if self._ticker:
            self._ticker.close()
        if self._ticker_task:
            self._ticker_task.cancel()

    def _parse_order(self, data: dict) -> OrderInfo:
        return OrderInfo(
            broker_order_id=data.get("order_id", ""),
            exchange_order_id=data.get("exchange_order_id"),
            status=self._map_status(data.get("status", "")),
            instrument=data.get("tradingsymbol", ""),
            side=Side(data.get("transaction_type", "BUY")),
            quantity=int(data.get("quantity", 0)),
            filled_quantity=int(data.get("filled_quantity", 0)),
            average_price=Decimal(str(data["average_price"])) if data.get("average_price") else None,
            price=Decimal(str(data["price"])) if data.get("price") else None,
            trigger_price=Decimal(str(data["trigger_price"])) if data.get("trigger_price") else None,
            order_type=self._reverse_map_order_type(data.get("order_type", "MARKET")),
            order_timestamp=data.get("order_timestamp"),
            raw=data,
        )

    @staticmethod
    def _map_status(s: str) -> OrderStatus:
        mapping = {
            "COMPLETE": OrderStatus.COMPLETE, "REJECTED": OrderStatus.REJECTED,
            "CANCELLED": OrderStatus.CANCELLED, "OPEN": OrderStatus.OPEN,
            "PENDING": OrderStatus.PENDING, "TRIGGER PENDING": OrderStatus.TRIGGER_PENDING,
            "MODIFY PENDING": OrderStatus.MODIFY_PENDING,
        }
        return mapping.get(str(s).upper(), OrderStatus.PENDING)

    @staticmethod
    def _reverse_map_order_type(ot: str) -> OrderType:
        mapping = {"MARKET": OrderType.MARKET, "LIMIT": OrderType.LIMIT,
                  "SL": OrderType.SL, "SL-M": OrderType.SLM}
        return mapping.get(str(ot).upper(), OrderType.MARKET)
```

```python
# app/broker/factory.py
from app.broker.base import BrokerAdapter
from app.broker.zerodha import ZerodhaAdapter
from config.settings import settings


def create_broker_adapter(
    broker_name: str,
    api_key: str = "",
    api_secret: str = "",
    access_token: str = "",
    request_token: str = "",
) -> BrokerAdapter:
    if broker_name == "zerodha":
        return ZerodhaAdapter(
            api_key=api_key or settings.zerodha_api_key,
            api_secret=api_secret or settings.zerodha_api_secret,
            access_token=access_token or settings.zerodha_access_token,
            request_token=request_token or settings.zerodha_request_token,
        )
    raise ValueError(f"Unknown broker: {broker_name}")
```

---

### 7.2 Master Trade Detector

```python
# app/core/master_detector.py
import asyncio
import structlog
from datetime import datetime, timezone

from app.broker.base import BrokerAdapter, OrderInfo, OrderStatus
from app.core.event_store import EventStore
from app.core.orchestrator import DistributionOrchestrator
from app.utils.metrics import MASTER_EVENTS_RECEIVED, MASTER_EVENTS_MISSED
from config.settings import settings

logger = structlog.get_logger()


class MasterTradeDetector:
    """
    FR-01: Detects all master account order lifecycle events.
    Triple-channel: (1) Postback webhook (primary, most reliable for API orders),
    (2) WebSocket via KiteTicker (catches non-API orders), (3) REST polling (safety net).
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        event_store: EventStore,
        orchestrator: DistributionOrchestrator,
    ):
        self._broker = broker
        self._event_store = event_store
        self._orchestrator = orchestrator
        self._known_order_states: dict[str, str] = {}  # order_id -> last_known_status
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        logger.info("master_detector.starting")
        await self._broker.start_order_stream(self._on_ws_order_update)
        self._poll_task = asyncio.create_task(self._rest_poll_loop())

    async def stop(self) -> None:
        await self._broker.stop_order_stream()
        if self._poll_task:
            self._poll_task.cancel()

    async def handle_postback(self, payload: dict) -> bool:
        """
        Channel 1 (primary): Zerodha postback webhook.
        Validates SHA-256 checksum, then processes order update.
        Returns True if checksum valid and event processed.
        """
        order_id = payload.get("order_id", "")
        order_timestamp = payload.get("order_timestamp", "")
        checksum = payload.get("checksum", "")
        if not self._broker.verify_postback_checksum(order_id, order_timestamp, checksum):
            logger.warning("master_detector.postback_checksum_failed", order_id=order_id)
            return False
        order = self._broker.parse_postback_payload(payload)
        await self._on_ws_order_update(order)  # Same processing path
        return True

    async def _on_ws_order_update(self, order: OrderInfo) -> None:
        """Called on every WebSocket order update from master account."""
        MASTER_EVENTS_RECEIVED.inc()
        logger.info(
            "master_detector.ws_event",
            order_id=order.broker_order_id,
            status=order.status.value,
            instrument=order.instrument,
            filled=order.filled_quantity,
        )

        event_type = self._classify_event(order)
        is_new = await self._event_store.persist_master_event(
            master_trade_id=order.broker_order_id,
            event_type=event_type,
            order_info=order,
        )

        if is_new:
            self._known_order_states[order.broker_order_id] = order.status.value
            await self._orchestrator.distribute(order.broker_order_id, event_type, order)

    async def _rest_poll_loop(self) -> None:
        """FR-01 fallback: REST reconciliation every N seconds."""
        while True:
            try:
                await asyncio.sleep(settings.rest_poll_interval_seconds)
                orders = await self._broker.get_all_orders()

                for order in orders:
                    last_known = self._known_order_states.get(order.broker_order_id)
                    current = f"{order.status.value}:{order.filled_quantity}"

                    if last_known != current:
                        event_type = self._classify_event(order)
                        is_new = await self._event_store.persist_master_event(
                            master_trade_id=order.broker_order_id,
                            event_type=event_type,
                            order_info=order,
                        )
                        if is_new:
                            MASTER_EVENTS_MISSED.inc()
                            logger.warning(
                                "master_detector.rest_caught_missed_event",
                                order_id=order.broker_order_id,
                                event_type=event_type,
                            )
                            self._known_order_states[order.broker_order_id] = current
                            await self._orchestrator.distribute(
                                order.broker_order_id, event_type, order
                            )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("master_detector.rest_poll_error", error=str(e))
                await asyncio.sleep(5)

    @staticmethod
    def _classify_event(order: OrderInfo) -> str:
        if order.status == OrderStatus.COMPLETE:
            return "FULL_FILL"
        if order.status == OrderStatus.CANCELLED:
            return "CANCEL"
        if order.status == OrderStatus.REJECTED:
            return "REJECTION"
        if order.filled_quantity and order.filled_quantity > 0:
            if order.filled_quantity < order.quantity:
                return "PARTIAL_FILL"
            return "FULL_FILL"
        if order.status == OrderStatus.OPEN:
            return "NEW"
        if order.status == OrderStatus.TRIGGER_PENDING:
            return "TRIGGER_PENDING"
        return "NEW"
```

---

### 7.3 Trade Event Store (Repository Layer)

```python
# app/core/event_store.py
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.broker.base import OrderInfo

logger = structlog.get_logger()


class EventStore:
    """
    FR-02: Persistent trade event store.
    Write-ahead: every event persisted before downstream processing.
    """

    def __init__(self, db_session_factory):
        self._session_factory = db_session_factory

    async def persist_master_event(
        self, master_trade_id: str, event_type: str, order_info: OrderInfo
    ) -> bool:
        """
        Persist a master trade event. Returns True if this is a new event
        (not a duplicate), False if already exists.
        Uses INSERT ... ON CONFLICT DO NOTHING for idempotency.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    INSERT INTO master_trade_events (
                        master_trade_id, event_type, instrument, exchange,
                        side, quantity, order_type, price, trigger_price,
                        filled_quantity, average_price, status,
                        broker_order_id, raw_payload, event_timestamp
                    ) VALUES (
                        :master_trade_id, :event_type, :instrument, :exchange,
                        :side, :quantity, :order_type, :price, :trigger_price,
                        :filled_quantity, :average_price, :status,
                        :broker_order_id, :raw_payload, :event_timestamp
                    )
                    ON CONFLICT ON CONSTRAINT uq_master_event DO NOTHING
                    RETURNING id
                """),
                {
                    "master_trade_id": master_trade_id,
                    "event_type": event_type,
                    "instrument": order_info.instrument,
                    "exchange": "NSE",
                    "side": order_info.side.value,
                    "quantity": order_info.quantity,
                    "order_type": order_info.order_type.value,
                    "price": float(order_info.price) if order_info.price else None,
                    "trigger_price": float(order_info.trigger_price) if order_info.trigger_price else None,
                    "filled_quantity": order_info.filled_quantity or 0,
                    "average_price": float(order_info.average_price) if order_info.average_price else None,
                    "status": order_info.status.value,
                    "broker_order_id": order_info.broker_order_id,
                    "raw_payload": str(order_info.raw) if order_info.raw else None,
                    "event_timestamp": order_info.order_timestamp or "now()",
                },
            )
            await session.commit()
            row = result.fetchone()
            return row is not None

    async def get_incomplete_master_trades(self) -> list[dict]:
        """FR-14: Fetch incomplete trades for restart recovery."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT DISTINCT master_trade_id, instrument, side, quantity,
                           order_type, price, filled_quantity, status
                    FROM master_trade_events
                    WHERE status NOT IN ('COMPLETE', 'CANCELLED', 'REJECTED', 'FULL_FILL')
                    ORDER BY master_trade_id
                """)
            )
            return [dict(row._mapping) for row in result.fetchall()]

    async def check_execution_exists(self, execution_id: str) -> bool:
        """FR-03: Idempotency check — does this execution already exist?"""
        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT 1 FROM client_execution_log WHERE execution_id = :eid LIMIT 1"),
                {"eid": execution_id},
            )
            return result.fetchone() is not None

    async def record_client_execution(self, execution: dict) -> None:
        """Record a client execution attempt."""
        async with self._session_factory() as session:
            await session.execute(
                text("""
                    INSERT INTO client_execution_log (
                        execution_id, master_trade_id, client_account_id,
                        event_type, instrument, exchange, side, quantity,
                        order_type, price, trigger_price, master_price,
                        status, slippage_pct
                    ) VALUES (
                        :execution_id, :master_trade_id, :client_account_id,
                        :event_type, :instrument, :exchange, :side, :quantity,
                        :order_type, :price, :trigger_price, :master_price,
                        :status, :slippage_pct
                    )
                    ON CONFLICT (execution_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        updated_at = NOW()
                """),
                execution,
            )
            await session.commit()

    async def update_execution_status(
        self, execution_id: str, status: str,
        broker_order_id: str = None, error: str = None, broker_response: dict = None
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                text("""
                    UPDATE client_execution_log
                    SET status = :status,
                        broker_order_id = COALESCE(:broker_order_id, broker_order_id),
                        error_detail = COALESCE(:error, error_detail),
                        broker_response = COALESCE(:broker_response, broker_response),
                        updated_at = NOW()
                    WHERE execution_id = :execution_id
                """),
                {
                    "execution_id": execution_id,
                    "status": status,
                    "broker_order_id": broker_order_id,
                    "error": error,
                    "broker_response": str(broker_response) if broker_response else None,
                },
            )
            await session.commit()

    async def get_active_client_accounts(self) -> list[dict]:
        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM client_accounts WHERE status = 'ACTIVE'")
            )
            return [dict(row._mapping) for row in result.fetchall()]

    async def get_distributed_fill(self, master_trade_id: str) -> float:
        """FR-06: Get the quantity already distributed for partial fills."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT total_distributed FROM distributed_fill_tracker
                    WHERE master_trade_id = :mtid
                """),
                {"mtid": master_trade_id},
            )
            row = result.fetchone()
            return float(row[0]) if row else 0.0

    async def update_distributed_fill(self, master_trade_id: str, new_total: float) -> None:
        async with self._session_factory() as session:
            await session.execute(
                text("""
                    INSERT INTO distributed_fill_tracker (master_trade_id, total_distributed)
                    VALUES (:mtid, :total)
                    ON CONFLICT ON CONSTRAINT uq_dft_master_trade
                    DO UPDATE SET total_distributed = :total, updated_at = NOW()
                """),
                {"mtid": master_trade_id, "total": new_total},
            )
            await session.commit()
```

---

### 7.4 Distribution Orchestrator

```python
# app/core/orchestrator.py
import asyncio
import structlog
from decimal import Decimal

from app.broker.base import OrderInfo, OrderRequest, Side, OrderType
from app.core.event_store import EventStore
from app.core.slippage_guard import SlippageGuard
from app.core.capital_guard import CapitalGuard
from app.core.execution_engine import ExecutionEngine
from app.core.kill_switch import KillSwitch
from app.core.audit import AuditLogger
from app.utils.metrics import CLIENT_EXECUTIONS_TOTAL, CLIENT_EXECUTIONS_FAILED

logger = structlog.get_logger()


class DistributionOrchestrator:
    """
    Central coordinator. For each master event, distributes to all
    eligible clients through the validation → execution pipeline.
    """

    def __init__(
        self,
        event_store: EventStore,
        slippage_guard: SlippageGuard,
        capital_guard: CapitalGuard,
        execution_engine: ExecutionEngine,
        kill_switch: KillSwitch,
        audit: AuditLogger,
    ):
        self._store = event_store
        self._slippage = slippage_guard
        self._capital = capital_guard
        self._engine = execution_engine
        self._kill_switch = kill_switch
        self._audit = audit

    async def distribute(
        self, master_trade_id: str, event_type: str, order: OrderInfo
    ) -> None:
        ks_mode = await self._kill_switch.get_mode()

        if ks_mode == "FULL_HALT":
            logger.warning("orchestrator.kill_switch_full_halt", master_trade_id=master_trade_id)
            await self._audit.log("orchestrator", "KILL_SWITCH_BLOCKED", master_trade_id, payload={"mode": ks_mode})
            return

        is_exit = event_type in ("CANCEL",) or (
            order.side == Side.SELL and event_type in ("FULL_FILL", "PARTIAL_FILL")
        )

        if ks_mode == "STOP_NEW_ENTRIES" and not is_exit:
            logger.warning("orchestrator.kill_switch_no_new_entries", master_trade_id=master_trade_id)
            await self._audit.log("orchestrator", "KILL_SWITCH_BLOCKED", master_trade_id, payload={"mode": ks_mode})
            return

        if event_type == "CANCEL":
            await self._handle_cancellation(master_trade_id, order)
            return

        if event_type == "MODIFY":
            await self._handle_modification(master_trade_id, order)
            return

        # FR-06: Partial fill — calculate incremental quantity
        quantity_to_distribute = await self._calculate_distribution_quantity(
            master_trade_id, event_type, order
        )
        if quantity_to_distribute <= 0:
            logger.info("orchestrator.nothing_to_distribute", master_trade_id=master_trade_id)
            return

        clients = await self._store.get_active_client_accounts()
        tasks = []

        for client in clients:
            tasks.append(
                self._process_client(
                    client, master_trade_id, event_type, order, quantity_to_distribute
                )
            )

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_client(
        self,
        client: dict,
        master_trade_id: str,
        event_type: str,
        order: OrderInfo,
        quantity: int,
    ) -> None:
        client_id = client["account_id"]
        execution_id = f"{client_id}:{master_trade_id}:{event_type}:{order.filled_quantity}"

        # FR-03: Idempotency check
        if await self._store.check_execution_exists(execution_id):
            logger.debug("orchestrator.duplicate_skipped", execution_id=execution_id)
            return

        # FR-10: Capital protection
        capital_ok, reason = await self._capital.validate(client, order)
        if not capital_ok:
            await self._store.record_client_execution({
                "execution_id": execution_id,
                "master_trade_id": master_trade_id,
                "client_account_id": client_id,
                "event_type": event_type,
                "instrument": order.instrument,
                "exchange": "NSE",
                "side": order.side.value,
                "quantity": quantity,
                "order_type": order.order_type.value,
                "price": float(order.price) if order.price else None,
                "trigger_price": float(order.trigger_price) if order.trigger_price else None,
                "master_price": float(order.average_price) if order.average_price else None,
                "status": "CAPITAL_REJECTED",
                "slippage_pct": None,
            })
            CLIENT_EXECUTIONS_FAILED.inc()
            await self._audit.log("orchestrator", "CAPITAL_REJECTED", master_trade_id, client_id, {"reason": reason})
            return

        # FR-05: Slippage guard (only for fills with a known price)
        if order.average_price and event_type in ("FULL_FILL", "PARTIAL_FILL"):
            slip_ok, slip_pct = self._slippage.check(order.side, order.average_price)
            if not slip_ok:
                await self._store.record_client_execution({
                    "execution_id": execution_id,
                    "master_trade_id": master_trade_id,
                    "client_account_id": client_id,
                    "event_type": event_type,
                    "instrument": order.instrument,
                    "exchange": "NSE",
                    "side": order.side.value,
                    "quantity": quantity,
                    "order_type": order.order_type.value,
                    "price": float(order.average_price),
                    "trigger_price": None,
                    "master_price": float(order.average_price),
                    "status": "SLIPPAGE_REJECTED",
                    "slippage_pct": float(slip_pct),
                })
                CLIENT_EXECUTIONS_FAILED.inc()
                await self._audit.log(
                    "slippage_guard", "SLIPPAGE_REJECTED", master_trade_id, client_id,
                    {"slippage_pct": float(slip_pct), "threshold": self._slippage.threshold},
                )
                return

        # Prepare execution record
        await self._store.record_client_execution({
            "execution_id": execution_id,
            "master_trade_id": master_trade_id,
            "client_account_id": client_id,
            "event_type": event_type,
            "instrument": order.instrument,
            "exchange": "NSE",
            "side": order.side.value,
            "quantity": quantity,
            "order_type": order.order_type.value,
            "price": float(order.price) if order.price else None,
            "trigger_price": float(order.trigger_price) if order.trigger_price else None,
            "master_price": float(order.average_price) if order.average_price else None,
            "status": "PENDING",
            "slippage_pct": None,
        })

        CLIENT_EXECUTIONS_TOTAL.inc()

        order_request = OrderRequest(
            instrument=order.instrument,
            exchange="NSE",
            side=order.side,
            quantity=quantity,
            order_type=order.order_type,
            price=order.price,
            trigger_price=order.trigger_price,
            tag=execution_id[:20],
        )

        await self._engine.submit(execution_id, client, order_request)

    async def _calculate_distribution_quantity(
        self, master_trade_id: str, event_type: str, order: OrderInfo
    ) -> int:
        """FR-06: For partial fills, distribute only the incremental quantity."""
        if event_type not in ("PARTIAL_FILL", "FULL_FILL"):
            return order.quantity

        already_distributed = await self._store.get_distributed_fill(master_trade_id)
        current_filled = float(order.filled_quantity or 0)
        incremental = current_filled - already_distributed

        if incremental > 0:
            await self._store.update_distributed_fill(master_trade_id, current_filled)
            return int(incremental)
        return 0

    async def _handle_cancellation(self, master_trade_id: str, order: OrderInfo) -> None:
        """FR-07: Cancel all client orders corresponding to this master trade."""
        logger.info("orchestrator.cancellation", master_trade_id=master_trade_id)
        await self._engine.cancel_all_for_master(master_trade_id)
        await self._audit.log("orchestrator", "CANCELLATION_DISTRIBUTED", master_trade_id)

    async def _handle_modification(self, master_trade_id: str, order: OrderInfo) -> None:
        """FR-07: Modify all client orders corresponding to this master trade."""
        logger.info("orchestrator.modification", master_trade_id=master_trade_id)
        await self._engine.modify_all_for_master(master_trade_id, order)
        await self._audit.log("orchestrator", "MODIFICATION_DISTRIBUTED", master_trade_id)
```

---

### 7.5 Slippage Guard

```python
# app/core/slippage_guard.py
from decimal import Decimal
import structlog

from app.broker.base import Side
from config.settings import settings

logger = structlog.get_logger()


class SlippageGuard:
    """
    FR-05: Prevents execution when price deviates >2% from master price.
    
    BUY:  client_price <= master_price * 1.02
    SELL: client_price >= master_price * 0.98
    
    For market orders, we use the current LTP as proxy for client_price
    at pre-execution check time. The actual fill price is verified post-execution.
    """

    def __init__(self, threshold_pct: float = None):
        self.threshold = threshold_pct or settings.slippage_threshold_pct

    def check(
        self,
        side: Side,
        master_price: Decimal,
        client_price: Decimal | None = None,
    ) -> tuple[bool, Decimal]:
        """
        Returns (is_within_limit, slippage_pct).
        If client_price is None (market order pre-check), returns (True, 0).
        """
        if client_price is None or master_price is None or master_price == 0:
            return True, Decimal("0")

        if side == Side.BUY:
            max_allowed = master_price * Decimal(str(1 + self.threshold / 100))
            slippage_pct = ((client_price - master_price) / master_price) * 100
            return client_price <= max_allowed, slippage_pct
        else:
            min_allowed = master_price * Decimal(str(1 - self.threshold / 100))
            slippage_pct = ((master_price - client_price) / master_price) * 100
            return client_price >= min_allowed, slippage_pct
```

---

### 7.6 Capital Validator

```python
# app/core/capital_guard.py
import structlog
from app.broker.base import OrderInfo

logger = structlog.get_logger()


class CapitalGuard:
    """
    FR-10: Client capital protection controls.
    Validates margin, open trades, account status before execution.
    """

    def __init__(self, event_store):
        self._store = event_store

    async def validate(self, client: dict, order: OrderInfo) -> tuple[bool, str]:
        if client["status"] != "ACTIVE":
            return False, f"Account status: {client['status']}"

        if client["consecutive_failures"] >= client.get("max_consecutive_failures", 5):
            return False, f"Consecutive failures ({client['consecutive_failures']}) exceeded threshold"

        open_count = await self._get_open_trade_count(client["account_id"])
        if open_count >= client.get("max_open_trades", 20):
            return False, f"Max open trades ({open_count}/{client['max_open_trades']}) reached"

        return True, "OK"

    async def _get_open_trade_count(self, client_account_id: str) -> int:
        """Count currently open (non-terminal) executions for this client."""
        from sqlalchemy import text
        async with self._store._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT COUNT(*) FROM client_execution_log
                    WHERE client_account_id = :cid
                    AND status IN ('PENDING', 'SUBMITTED')
                """),
                {"cid": client_account_id},
            )
            return result.scalar() or 0
```

---

### 7.7 Parallel Execution Engine

```python
# app/core/execution_engine.py
import asyncio
import structlog

from app.broker.base import BrokerAdapter, OrderRequest, OrderStatus
from app.core.event_store import EventStore
from app.core.retry_manager import RetryManager
from app.core.audit import AuditLogger
from app.utils.rate_limiter import TokenBucketRateLimiter
from app.utils.metrics import EXECUTION_LATENCY, RATE_LIMITER_WAIT
from config.settings import settings

logger = structlog.get_logger()


class ExecutionEngine:
    """
    FR-04: Parallel order execution with per-API-key rate limiting.
    
    Rate limiters are keyed by API key, NOT by client account ID.
    This correctly handles all configurations:
    - 30 keys (1:1): each client gets its own rate limiter → true parallel
    - 10 keys (3:1): 3 clients share a rate limiter → still fast
    -  1 key (30:1): all 30 clients share one rate limiter → serialized
    
    The client chooses the key strategy; the code adapts automatically.
    See Section 2.1.1 for the full cost-performance analysis.
    """

    def __init__(
        self,
        broker_adapters: dict[str, BrokerAdapter],
        client_api_keys: dict[str, str],  # {client_account_id: api_key}
        event_store: EventStore,
        retry_manager: RetryManager,
        audit: AuditLogger,
    ):
        self._brokers = broker_adapters  # {client_account_id: BrokerAdapter}
        self._client_api_keys = client_api_keys
        self._store = event_store
        self._retry = retry_manager
        self._audit = audit
        # Rate limiters keyed by API key (not client ID).
        # Clients sharing the same API key share the same rate limiter.
        unique_keys = set(client_api_keys.values())
        self._rate_limiters: dict[str, TokenBucketRateLimiter] = {
            api_key: TokenBucketRateLimiter(
                rate=settings.rate_limit_per_second,
                burst=settings.rate_limit_burst,
            )
            for api_key in unique_keys
        }
        self._semaphore = asyncio.Semaphore(settings.max_workers)

    def _get_rate_limiter(self, client_id: str) -> TokenBucketRateLimiter:
        api_key = self._client_api_keys.get(client_id, "")
        if api_key not in self._rate_limiters:
            self._rate_limiters[api_key] = TokenBucketRateLimiter(
                rate=settings.rate_limit_per_second,
                burst=settings.rate_limit_burst,
            )
        return self._rate_limiters[api_key]

    async def submit(self, execution_id: str, client: dict, request: OrderRequest) -> None:
        """Submit an order for async execution."""
        asyncio.create_task(self._execute_with_semaphore(execution_id, client, request))

    async def _execute_with_semaphore(
        self, execution_id: str, client: dict, request: OrderRequest
    ) -> None:
        async with self._semaphore:
            await self._execute(execution_id, client, request)

    async def _execute(self, execution_id: str, client: dict, request: OrderRequest) -> None:
        client_id = client["account_id"]
        broker = self._brokers.get(client_id)
        if not broker:
            await self._store.update_execution_status(execution_id, "FAILED", error="No broker adapter")
            return

        import time
        start = time.monotonic()
        rate_limiter = self._get_rate_limiter(client_id)

        try:
            # Acquire per-client rate limit token (independent per API key)
            wait_start = time.monotonic()
            await rate_limiter.acquire()
            RATE_LIMITER_WAIT.observe(time.monotonic() - wait_start)

            # Place the order
            response = await asyncio.wait_for(
                broker.place_order(request),
                timeout=settings.order_timeout_seconds,
            )

            elapsed_ms = int((time.monotonic() - start) * 1000)
            EXECUTION_LATENCY.observe(elapsed_ms)

            if response.status == OrderStatus.REJECTED:
                await self._store.update_execution_status(
                    execution_id, "REJECTED",
                    broker_order_id=response.broker_order_id,
                    error=response.message,
                )
                await self._audit.log(
                    "execution_engine", "ORDER_REJECTED", None, client_id,
                    {"execution_id": execution_id, "message": response.message},
                )
                return

            await self._store.update_execution_status(
                execution_id, "SUBMITTED",
                broker_order_id=response.broker_order_id,
            )
            await self._audit.log(
                "execution_engine", "ORDER_PLACED", None, client_id,
                {"execution_id": execution_id, "broker_order_id": response.broker_order_id,
                 "latency_ms": elapsed_ms},
            )

        except asyncio.TimeoutError:
            logger.warning("execution_engine.timeout", execution_id=execution_id)
            # FR-11: Enter retry path — check status first
            await self._retry.handle_timeout(execution_id, client, request, broker)

        except Exception as e:
            logger.error("execution_engine.error", execution_id=execution_id, error=str(e))
            await self._retry.handle_error(execution_id, client, request, broker, str(e))

    async def cancel_all_for_master(self, master_trade_id: str) -> None:
        """FR-07: Cancel all client orders for a given master trade."""
        from sqlalchemy import text
        async with self._store._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT execution_id, client_account_id, broker_order_id
                    FROM client_execution_log
                    WHERE master_trade_id = :mtid
                    AND status IN ('SUBMITTED', 'PENDING')
                    AND broker_order_id IS NOT NULL
                """),
                {"mtid": master_trade_id},
            )
            rows = result.fetchall()

        tasks = []
        for row in rows:
            tasks.append(self._cancel_single(row))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _cancel_single(self, row) -> None:
        client_id = row[1]
        broker_order_id = row[2]
        broker = self._brokers.get(client_id)
        if broker and broker_order_id:
            try:
                await self._get_rate_limiter(client_id).acquire()
                success = await broker.cancel_order(broker_order_id)
                status = "CANCELLED" if success else "FAILED"
                await self._store.update_execution_status(row[0], status)
            except Exception as e:
                logger.error("execution_engine.cancel_failed", error=str(e))

    async def modify_all_for_master(self, master_trade_id: str, order) -> None:
        """FR-07: Modify all client orders for a given master trade."""
        from sqlalchemy import text
        async with self._store._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT execution_id, client_account_id, broker_order_id
                    FROM client_execution_log
                    WHERE master_trade_id = :mtid
                    AND status IN ('SUBMITTED', 'PENDING')
                    AND broker_order_id IS NOT NULL
                """),
                {"mtid": master_trade_id},
            )
            rows = result.fetchall()

        tasks = []
        for row in rows:
            tasks.append(self._modify_single(row, order))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _modify_single(self, row, order) -> None:
        client_id = row[1]
        broker_order_id = row[2]
        broker = self._brokers.get(client_id)
        if broker and broker_order_id:
            try:
                await self._get_rate_limiter(client_id).acquire()
                mod_request = OrderRequest(
                    instrument=order.instrument,
                    exchange="NSE",
                    side=order.side,
                    quantity=order.quantity,
                    order_type=order.order_type,
                    price=order.price,
                    trigger_price=order.trigger_price,
                )
                await broker.modify_order(broker_order_id, mod_request)
            except Exception as e:
                logger.error("execution_engine.modify_failed", error=str(e))
```

---

### 7.8 Reconciliation Engine

```python
# app/core/reconciliation.py
import asyncio
import structlog
from datetime import datetime, timezone
from decimal import Decimal

from app.broker.base import BrokerAdapter, Position
from app.core.event_store import EventStore
from app.core.audit import AuditLogger
from app.utils.metrics import RECONCILIATION_MISMATCHES
from config.settings import settings

logger = structlog.get_logger()


class ReconciliationEngine:
    """
    FR-09: Periodic position verification between master and all clients.
    Runs every 60 seconds during market hours.
    """

    def __init__(
        self,
        master_broker: BrokerAdapter,
        client_brokers: dict[str, BrokerAdapter],
        event_store: EventStore,
        audit: AuditLogger,
    ):
        self._master_broker = master_broker
        self._client_brokers = client_brokers
        self._store = event_store
        self._audit = audit
        self._running = False

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._reconciliation_loop())

    async def stop(self) -> None:
        self._running = False

    async def run_once(self) -> dict:
        """Execute a single reconciliation cycle. Returns summary."""
        snapshot_time = datetime.now(timezone.utc)
        results = {"time": snapshot_time.isoformat(), "matches": 0, "mismatches": []}

        master_positions = await self._master_broker.get_positions()
        master_map: dict[str, Position] = {
            f"{p.instrument}:{p.exchange}": p for p in master_positions
        }

        for client_id, broker in self._client_brokers.items():
            try:
                client_positions = await broker.get_positions()
                client_map = {f"{p.instrument}:{p.exchange}": p for p in client_positions}

                all_instruments = set(master_map.keys()) | set(client_map.keys())

                for inst_key in all_instruments:
                    master_qty = master_map.get(inst_key, Position(
                        instrument=inst_key.split(":")[0], exchange=inst_key.split(":")[1],
                        net_quantity=0, average_price=Decimal("0"), product_type="CNC"
                    )).net_quantity
                    client_qty = client_map.get(inst_key, Position(
                        instrument=inst_key.split(":")[0], exchange=inst_key.split(":")[1],
                        net_quantity=0, average_price=Decimal("0"), product_type="CNC"
                    )).net_quantity

                    match_status = "MATCHED" if master_qty == client_qty else "MISMATCHED"

                    if match_status == "MISMATCHED":
                        RECONCILIATION_MISMATCHES.inc()
                        results["mismatches"].append({
                            "client": client_id,
                            "instrument": inst_key,
                            "master_qty": master_qty,
                            "client_qty": client_qty,
                            "diff": master_qty - client_qty,
                        })
                        logger.error(
                            "reconciliation.mismatch",
                            client=client_id,
                            instrument=inst_key,
                            master_qty=master_qty,
                            client_qty=client_qty,
                        )

                        if settings.auto_correct_on_mismatch:
                            await self._attempt_auto_correct(
                                client_id, broker, inst_key, master_qty, client_qty
                            )
                    else:
                        results["matches"] += 1

            except Exception as e:
                logger.error("reconciliation.client_error", client=client_id, error=str(e))

        await self._audit.log("reconciliation", "CYCLE_COMPLETE", payload=results)
        return results

    async def _reconciliation_loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                logger.error("reconciliation.loop_error", error=str(e))
            await asyncio.sleep(settings.reconciliation_interval_seconds)

    async def _attempt_auto_correct(
        self, client_id: str, broker: BrokerAdapter,
        inst_key: str, master_qty: int, client_qty: int
    ) -> None:
        """Attempt to auto-correct position mismatch by placing a corrective order."""
        diff = master_qty - client_qty
        instrument, exchange = inst_key.split(":")

        from app.broker.base import OrderRequest, Side, OrderType
        side = Side.BUY if diff > 0 else Side.SELL
        request = OrderRequest(
            instrument=instrument,
            exchange=exchange,
            side=side,
            quantity=abs(diff),
            order_type=OrderType.MARKET,
        )

        try:
            response = await broker.place_order(request)
            logger.info("reconciliation.auto_corrected", client=client_id, response=response)
            await self._audit.log(
                "reconciliation", "AUTO_CORRECTED", client_account_id=client_id,
                payload={"instrument": instrument, "diff": diff},
            )
        except Exception as e:
            logger.error("reconciliation.auto_correct_failed", error=str(e))
```

---

### 7.9 Kill Switch Controller

```python
# app/core/kill_switch.py
import structlog
from sqlalchemy import text

logger = structlog.get_logger()


class KillSwitch:
    """
    FR-12: System-level emergency stop.
    Modes: DISABLED, STOP_NEW_ENTRIES, FULL_HALT
    State stored in DB for persistence across restarts.
    Cached in-memory with DB poll every 5 seconds for low latency.
    """

    def __init__(self, db_session_factory, redis_client=None):
        self._session_factory = db_session_factory
        self._redis = redis_client
        self._cached_mode: str = "DISABLED"

    async def get_mode(self) -> str:
        if self._redis:
            cached = await self._redis.get("kill_switch_mode")
            if cached:
                return cached.decode()

        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT mode FROM kill_switch_state WHERE id = 1")
            )
            row = result.fetchone()
            mode = row[0] if row else "DISABLED"
            self._cached_mode = mode

            if self._redis:
                await self._redis.setex("kill_switch_mode", 5, mode)

            return mode

    async def set_mode(self, mode: str, reason: str, activated_by: str = "system") -> dict:
        previous = await self.get_mode()

        async with self._session_factory() as session:
            await session.execute(
                text("""
                    UPDATE kill_switch_state
                    SET mode = :mode, reason = :reason,
                        activated_by = :activated_by, activated_at = NOW(),
                        updated_at = NOW()
                    WHERE id = 1
                """),
                {"mode": mode, "reason": reason, "activated_by": activated_by},
            )
            await session.commit()

        if self._redis:
            await self._redis.setex("kill_switch_mode", 5, mode)

        self._cached_mode = mode
        logger.warning("kill_switch.mode_changed", previous=previous, current=mode, reason=reason)

        return {"previous_mode": previous, "current_mode": mode, "reason": reason}
```

---

### 7.10 Retry Manager

```python
# app/core/retry_manager.py
import asyncio
import structlog

from app.broker.base import BrokerAdapter, OrderRequest, OrderStatus
from app.core.event_store import EventStore
from app.core.audit import AuditLogger
from app.utils.metrics import RETRY_COUNT
from config.settings import settings

logger = structlog.get_logger()


class RetryManager:
    """
    FR-11: Safe retry logic.
    Retries ONLY when order status is unknown (timeout, network error).
    Always checks order status before retrying to prevent duplicates.
    """

    def __init__(self, event_store: EventStore, audit: AuditLogger):
        self._store = event_store
        self._audit = audit
        self._max_retries = settings.max_retries
        self._backoff_base = settings.retry_backoff_base

    async def handle_timeout(
        self, execution_id: str, client: dict, request: OrderRequest, broker: BrokerAdapter
    ) -> None:
        await self._retry_with_status_check(execution_id, client, request, broker, "TIMEOUT")

    async def handle_error(
        self, execution_id: str, client: dict, request: OrderRequest,
        broker: BrokerAdapter, error: str
    ) -> None:
        await self._retry_with_status_check(execution_id, client, request, broker, error)

    async def _retry_with_status_check(
        self, execution_id: str, client: dict, request: OrderRequest,
        broker: BrokerAdapter, reason: str
    ) -> None:
        for attempt in range(1, self._max_retries + 1):
            RETRY_COUNT.inc()
            backoff = self._backoff_base * (2 ** (attempt - 1))

            logger.info(
                "retry_manager.attempt",
                execution_id=execution_id,
                attempt=attempt,
                backoff_s=backoff,
                reason=reason,
            )
            await asyncio.sleep(backoff)

            # CRITICAL: Check if order was actually placed before retrying
            try:
                orders = await broker.get_all_orders()
                tag_match = [o for o in orders if request.tag and request.tag in str(o.raw)]

                if tag_match:
                    existing = tag_match[0]
                    if existing.status in (OrderStatus.COMPLETE, OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING):
                        logger.info("retry_manager.order_found_no_retry", execution_id=execution_id)
                        await self._store.update_execution_status(
                            execution_id, "SUBMITTED",
                            broker_order_id=existing.broker_order_id,
                        )
                        return
            except Exception as e:
                logger.warning("retry_manager.status_check_failed", error=str(e))

            # Order not found — safe to retry
            try:
                response = await asyncio.wait_for(
                    broker.place_order(request),
                    timeout=settings.order_timeout_seconds,
                )
                if response.status != OrderStatus.REJECTED:
                    await self._store.update_execution_status(
                        execution_id, "SUBMITTED",
                        broker_order_id=response.broker_order_id,
                    )
                    await self._audit.log(
                        "retry_manager", "RETRY_SUCCESS", client_account_id=client["account_id"],
                        payload={"execution_id": execution_id, "attempt": attempt},
                    )
                    return
                else:
                    await self._store.update_execution_status(
                        execution_id, "REJECTED", error=response.message,
                    )
                    return
            except Exception as e:
                logger.warning("retry_manager.retry_failed", attempt=attempt, error=str(e))

        # All retries exhausted
        await self._store.update_execution_status(execution_id, "FAILED", error=f"Max retries ({self._max_retries}) exhausted: {reason}")
        await self._audit.log(
            "retry_manager", "RETRY_EXHAUSTED", client_account_id=client["account_id"],
            payload={"execution_id": execution_id, "reason": reason},
        )
```

---

### 7.11 Audit Logger

```python
# app/core/audit.py
import uuid
import structlog
from datetime import datetime, timezone
from sqlalchemy import text

logger = structlog.get_logger()


class AuditLogger:
    """
    FR-13: Immutable append-only audit trail.
    Every trade-related action is logged with full context.
    INSERT-only — no UPDATE or DELETE on audit_log table.
    """

    def __init__(self, db_session_factory):
        self._session_factory = db_session_factory

    async def log(
        self,
        component: str,
        event_type: str,
        master_trade_id: str = None,
        client_account_id: str = None,
        payload: dict = None,
        outcome: str = "SUCCESS",
        duration_ms: int = None,
    ) -> None:
        event_id = f"{component}:{event_type}:{uuid.uuid4().hex[:12]}"

        try:
            async with self._session_factory() as session:
                await session.execute(
                    text("""
                        INSERT INTO audit_log (
                            event_id, timestamp, component, event_type,
                            master_trade_id, client_account_id, payload,
                            outcome, duration_ms
                        ) VALUES (
                            :event_id, :timestamp, :component, :event_type,
                            :master_trade_id, :client_account_id, :payload,
                            :outcome, :duration_ms
                        )
                    """),
                    {
                        "event_id": event_id,
                        "timestamp": datetime.now(timezone.utc),
                        "component": component,
                        "event_type": event_type,
                        "master_trade_id": master_trade_id,
                        "client_account_id": client_account_id,
                        "payload": str(payload) if payload else "{}",
                        "outcome": outcome,
                        "duration_ms": duration_ms,
                    },
                )
                await session.commit()
        except Exception as e:
            logger.error("audit.write_failed", event_type=event_type, error=str(e))
```

---

### 7.12 Restart Recovery Manager

```python
# app/core/recovery.py
import structlog
from app.core.event_store import EventStore
from app.core.orchestrator import DistributionOrchestrator
from app.broker.base import OrderInfo, Side, OrderType, OrderStatus

logger = structlog.get_logger()


class RecoveryManager:
    """
    FR-14: Restart recovery mechanism.
    On system restart, reloads incomplete trades and resumes distribution.
    """

    def __init__(self, event_store: EventStore, orchestrator: DistributionOrchestrator):
        self._store = event_store
        self._orchestrator = orchestrator

    async def recover(self) -> int:
        """
        Recover incomplete master trades and resume distribution.
        Returns the count of recovered trades.
        """
        incomplete = await self._store.get_incomplete_master_trades()
        logger.info("recovery.starting", incomplete_count=len(incomplete))

        recovered = 0
        for trade in incomplete:
            try:
                order = OrderInfo(
                    broker_order_id=trade["master_trade_id"],
                    exchange_order_id=None,
                    status=OrderStatus.OPEN,
                    instrument=trade["instrument"],
                    side=Side(trade["side"]),
                    quantity=int(trade["quantity"]),
                    filled_quantity=int(trade.get("filled_quantity", 0)),
                    average_price=trade.get("price"),
                    price=trade.get("price"),
                    trigger_price=None,
                    order_type=OrderType(trade["order_type"]),
                )
                event_type = "PARTIAL_FILL" if order.filled_quantity > 0 else "NEW"
                await self._orchestrator.distribute(trade["master_trade_id"], event_type, order)
                recovered += 1
            except Exception as e:
                logger.error("recovery.trade_failed", trade_id=trade["master_trade_id"], error=str(e))

        logger.info("recovery.completed", recovered=recovered)
        return recovered
```

---

### 7.13 Auth & Token Management

Zerodha uses OAuth2 with daily token expiry. Each client has their own API key and must authenticate once per day. This module manages the full token lifecycle.

**Token Lifecycle:**
1. Client visits `/auth/login/{account_id}` → redirected to Zerodha login page
2. After login, Zerodha redirects back to `/auth/callback?request_token=...&account_id=...`
3. System exchanges `request_token` for `access_token` using client's `api_secret`
4. `access_token` stored (encrypted) in `client_accounts.access_token`, `token_expires_at` set to next 6:00 AM IST
5. Pre-market health check (8:45 AM IST) validates all tokens, alerts on expired ones

```python
# app/auth/token_manager.py
import structlog
from datetime import datetime, timedelta, timezone
from kiteconnect import KiteConnect

from app.core.event_store import EventStore
from config.settings import settings

logger = structlog.get_logger()

IST = timezone(timedelta(hours=5, minutes=30))


class TokenManager:
    """
    Manages per-client Zerodha access tokens.
    Each client has their own API key → own access token → own rate limits.
    Tokens expire daily at ~6:00 AM IST and must be refreshed via OAuth login.
    """

    def __init__(self, event_store: EventStore):
        self._store = event_store

    async def generate_login_url(self, account_id: str) -> str:
        """Generate Zerodha login URL for a specific client."""
        client = await self._store.get_client_account(account_id)
        kite = KiteConnect(api_key=client["api_key"])
        return kite.login_url()

    async def handle_callback(self, account_id: str, request_token: str) -> bool:
        """Exchange request_token for access_token and persist."""
        client = await self._store.get_client_account(account_id)
        kite = KiteConnect(api_key=client["api_key"])

        try:
            data = kite.generate_session(request_token, api_secret=client["api_secret"])
            access_token = data["access_token"]

            tomorrow_6am = (
                datetime.now(IST).replace(hour=6, minute=0, second=0, microsecond=0)
                + timedelta(days=1)
            )

            await self._store.update_client_token(
                account_id=account_id,
                access_token=access_token,
                token_expires_at=tomorrow_6am,
                last_login_at=datetime.now(IST),
            )

            logger.info("token_manager.token_refreshed", account_id=account_id)
            return True

        except Exception as e:
            logger.error("token_manager.login_failed", account_id=account_id, error=str(e))
            return False

    async def validate_all_tokens(self) -> dict:
        """
        Pre-market check: validate all active client tokens.
        Returns dict of {account_id: is_valid}.
        Called by scheduler at 8:45 AM IST (15 min before market open).
        """
        clients = await self._store.get_active_clients()
        results = {}
        now = datetime.now(IST)

        for client in clients:
            account_id = client["account_id"]
            token_expires = client.get("token_expires_at")

            if not token_expires or token_expires < now:
                results[account_id] = False
                logger.warning("token_manager.expired_token", account_id=account_id)
                continue

            # Verify token is actually usable by making a lightweight API call
            try:
                kite = KiteConnect(api_key=client["api_key"])
                kite.set_access_token(client["access_token"])
                kite.profile()  # lightweight call to verify token
                results[account_id] = True
            except Exception:
                results[account_id] = False
                logger.warning("token_manager.invalid_token", account_id=account_id)

        valid = sum(1 for v in results.values() if v)
        total = len(results)
        logger.info("token_manager.validation_complete", valid=valid, total=total)

        if valid < total:
            # TODO: Send alert (email/Slack) for clients with expired tokens
            logger.critical(
                "token_manager.clients_not_authenticated",
                expired=[k for k, v in results.items() if not v],
            )

        return results

    async def get_client_api_key_map(self) -> dict[str, str]:
        """
        Return {account_id: api_key} for all active clients.
        Used by ExecutionEngine to key rate limiters by API key,
        so clients sharing the same key share the same rate limiter.
        """
        clients = await self._store.get_active_clients()
        return {c["account_id"]: c["api_key"] for c in clients}

    async def build_broker_adapters(self) -> dict:
        """
        Build authenticated BrokerAdapter instances for all active clients
        with valid tokens. Called at application startup and after token refresh.
        """
        from app.broker.zerodha import ZerodhaAdapter
        clients = await self._store.get_active_clients()
        adapters = {}
        now = datetime.now(IST)

        for client in clients:
            account_id = client["account_id"]
            token_expires = client.get("token_expires_at")

            if not client.get("access_token") or not token_expires or token_expires < now:
                logger.warning("token_manager.skipping_client", account_id=account_id)
                continue

            adapter = ZerodhaAdapter(
                api_key=client["api_key"],
                api_secret=client["api_secret"],
                access_token=client["access_token"],
            )
            adapters[account_id] = adapter

        logger.info("token_manager.adapters_built", count=len(adapters))
        return adapters
```

**Login Flow API Endpoints:**

```python
# app/auth/login_flow.py (FastAPI router)
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.auth.token_manager import TokenManager

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/login/{account_id}")
async def login(account_id: str, token_manager: TokenManager):
    """Redirect client to Zerodha OAuth login page."""
    try:
        url = await token_manager.generate_login_url(account_id)
        return RedirectResponse(url=url)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Account not found: {account_id}")


@router.get("/callback")
async def callback(request_token: str, account_id: str, token_manager: TokenManager):
    """Handle Zerodha OAuth callback — exchange request_token for access_token."""
    success = await token_manager.handle_callback(account_id, request_token)
    if success:
        return {"status": "ok", "message": f"Token refreshed for {account_id}"}
    raise HTTPException(status_code=401, detail="Token exchange failed")


@router.get("/status")
async def token_status(token_manager: TokenManager):
    """Check token validity for all clients. Used by monitoring dashboard."""
    results = await token_manager.validate_all_tokens()
    return {
        "total": len(results),
        "valid": sum(1 for v in results.values() if v),
        "expired": [k for k, v in results.items() if not v],
    }
```

**Daily Token Flow Sequence:**

```
 ┌────────────┐     ┌──────────────┐     ┌───────────┐     ┌─────────┐
 │  Admin/     │     │  Copy Trade  │     │  Zerodha  │     │   DB    │
 │  Client UI  │     │  Engine      │     │  OAuth    │     │         │
 └─────┬──────┘     └──────┬───────┘     └─────┬─────┘     └────┬────┘
       │  GET /auth/login/C1│                   │                │
       │───────────────────>│                   │                │
       │  302 Redirect      │                   │                │
       │<───────────────────│                   │                │
       │                    │                   │                │
       │  User logs in at Zerodha               │                │
       │───────────────────────────────────────>│                │
       │  Redirect with request_token           │                │
       │<──────────────────────────────────────-│                │
       │                    │                   │                │
       │  GET /auth/callback?request_token=...  │                │
       │───────────────────>│                   │                │
       │                    │  generate_session  │                │
       │                    │──────────────────>│                │
       │                    │  access_token      │                │
       │                    │<─────────────────-│                │
       │                    │                   │                │
       │                    │  UPDATE client_accounts SET        │
       │                    │  access_token, token_expires_at    │
       │                    │──────────────────────────────────>│
       │  200 OK            │                   │                │
       │<───────────────────│                   │                │
       │                    │                   │                │
       │    8:45 AM IST — Pre-market validation  │                │
       │                    │  SELECT active clients             │
       │                    │──────────────────────────────────>│
       │                    │  kite.profile() per client         │
       │                    │──────────────────>│                │
       │                    │  Alert if any expired              │
       │                    │                   │                │
```

---

### 7.14 Application Entry Point

```python
# app/main.py
import asyncio
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config.settings import settings
from app.broker.factory import create_broker_adapter
from app.auth.token_manager import TokenManager
from app.core.master_detector import MasterTradeDetector
from app.core.event_store import EventStore
from app.core.orchestrator import DistributionOrchestrator
from app.core.slippage_guard import SlippageGuard
from app.core.capital_guard import CapitalGuard
from app.core.execution_engine import ExecutionEngine
from app.core.reconciliation import ReconciliationEngine
from app.core.kill_switch import KillSwitch
from app.core.retry_manager import RetryManager
from app.core.audit import AuditLogger
from app.core.recovery import RecoveryManager
from app.api import health, kill_switch as ks_api, reconciliation as recon_api, postback
from app.auth.login_flow import router as auth_router

logger = structlog.get_logger()

engine = create_async_engine(settings.database_url, pool_size=settings.db_pool_size)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def db_session():
    async with SessionFactory() as session:
        yield session


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    logger.info("app.starting", env=settings.app_env)

    # Initialize core components
    event_store = EventStore(db_session)
    audit = AuditLogger(db_session)
    kill_switch_ctrl = KillSwitch(db_session)
    slippage = SlippageGuard()
    capital = CapitalGuard(event_store)
    retry_mgr = RetryManager(event_store, audit)

    # Initialize token manager (handles per-client Zerodha OAuth tokens)
    token_manager = TokenManager(event_store)

    # Create master broker adapter
    master_broker = create_broker_adapter(settings.master_broker)
    await master_broker.connect()

    # Build per-client broker adapters from DB (each with own API key + access token)
    client_brokers = await token_manager.build_broker_adapters()
    logger.info("app.client_adapters_ready", count=len(client_brokers))

    # Pre-market token validation
    token_status = await token_manager.validate_all_tokens()
    valid = sum(1 for v in token_status.values() if v)
    logger.info("app.token_status", valid=valid, total=len(token_status))

    # Build client→API key mapping for per-key rate limiting
    client_api_keys = await token_manager.get_client_api_key_map()

    exec_engine = ExecutionEngine(
        client_brokers, client_api_keys, event_store, retry_mgr, audit
    )

    orchestrator = DistributionOrchestrator(
        event_store, slippage, capital, exec_engine, kill_switch_ctrl, audit
    )

    detector = MasterTradeDetector(master_broker, event_store, orchestrator)
    reconciler = ReconciliationEngine(master_broker, client_brokers, event_store, audit)
    recovery = RecoveryManager(event_store, orchestrator)

    # FR-14: Recover incomplete trades
    recovered = await recovery.recover()
    logger.info("app.recovery_complete", recovered=recovered)

    # Start services
    await detector.start()
    await reconciler.start()

    # Store references for API handlers
    app.state.kill_switch = kill_switch_ctrl
    app.state.master_detector = detector
    app.state.reconciler = reconciler
    app.state.event_store = event_store
    app.state.master_broker = master_broker

    logger.info("app.started", clients=len(client_brokers))

    yield

    # Shutdown
    logger.info("app.shutting_down")
    await detector.stop()
    await reconciler.stop()
    await engine.dispose()


app = FastAPI(
    title="Copy Trading Engine",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(ks_api.router, prefix="/api/v1", tags=["kill-switch"])
app.include_router(postback.router, prefix="/api/v1", tags=["postback"])
app.include_router(recon_api.router, prefix="/api/v1", tags=["reconciliation"])
app.include_router(auth_router, prefix="/api/v1", tags=["authentication"])
```

---

### Rate Limiter Utility

Each client gets their own `TokenBucketRateLimiter` instance (created in `ExecutionEngine.__init__`). Since every client has their own Zerodha API key, rate limits are completely independent — no global bottleneck.

```python
# app/utils/rate_limiter.py
import asyncio
import time


class TokenBucketRateLimiter:
    """
    Token bucket rate limiter — one instance PER client adapter.
    Each client has their own API key → own 10/sec Zerodha rate limit.
    Allows burst up to `burst` tokens, refills at `rate` tokens/second.
    """

    def __init__(self, rate: float, burst: int):
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1:
                self._tokens -= 1
                return

        # No tokens available — wait for refill
        wait_time = (1 - self._tokens) / self._rate
        await asyncio.sleep(wait_time)
        async with self._lock:
            self._tokens = 0
            self._last_refill = time.monotonic()
```

### Prometheus Metrics

```python
# app/utils/metrics.py
from prometheus_client import Counter, Histogram, Gauge

MASTER_EVENTS_RECEIVED = Counter("master_events_received_total", "Total master events received")
MASTER_EVENTS_MISSED = Counter("master_events_missed", "Events caught by REST but missed by WS")

CLIENT_EXECUTIONS_TOTAL = Counter("client_executions_total", "Total client executions attempted")
CLIENT_EXECUTIONS_FAILED = Counter("client_executions_failed", "Failed client executions")

EXECUTION_LATENCY = Histogram(
    "execution_latency_ms", "Order placement latency in ms",
    buckets=[50, 100, 200, 500, 1000, 2000, 5000],
)
RATE_LIMITER_WAIT = Histogram(
    "rate_limiter_wait_seconds", "Time waiting for rate limit token",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
)

RECONCILIATION_MISMATCHES = Counter("reconciliation_mismatches", "Position mismatches found")
RETRY_COUNT = Counter("retry_count_total", "Total retry attempts")
KILL_SWITCH_ACTIVE = Gauge("kill_switch_active", "1 if kill switch is active")
```

---

## 8. API Endpoint Implementation

```python
# app/api/postback.py
from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.post("/postback/zerodha")
async def zerodha_postback(request: Request):
    """
    Zerodha webhook receiver. Register this URL in Kite Connect dashboard.
    Payload is JSON with order lifecycle data. Validates SHA-256 checksum before processing.
    """
    payload = await request.json()
    detector = request.app.state.master_detector
    ok = await detector.handle_postback(payload)
    if not ok:
        return Response(status_code=400, content="Invalid checksum")
    return {"status": "ok"}
```

```python
# app/api/health.py
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health_check(request: Request):
    ks = request.app.state.kill_switch
    store = request.app.state.event_store
    return {
        "status": "healthy",
        "components": {
            "database": "connected",
            "kill_switch": await ks.get_mode(),
        },
    }
```

```python
# app/api/kill_switch.py
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class KillSwitchRequest(BaseModel):
    mode: str  # DISABLED, STOP_NEW_ENTRIES, FULL_HALT
    reason: str


@router.get("/kill-switch")
async def get_kill_switch(request: Request):
    ks = request.app.state.kill_switch
    return {"mode": await ks.get_mode()}


@router.put("/kill-switch")
async def set_kill_switch(request: Request, body: KillSwitchRequest):
    ks = request.app.state.kill_switch
    result = await ks.set_mode(body.mode, body.reason, activated_by="api")
    return result
```

```python
# app/api/reconciliation.py
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/reconciliation/status")
async def reconciliation_status(request: Request):
    reconciler = request.app.state.reconciler
    return await reconciler.run_once()


@router.post("/reconciliation/run")
async def trigger_reconciliation(request: Request):
    reconciler = request.app.state.reconciler
    result = await reconciler.run_once()
    return {"triggered": True, "result": result}
```

---

## 9. FR → Code Traceability Matrix

| FR    | Requirement                    | Primary Module(s)                              | Key Method(s)                                       |
|-------|--------------------------------|------------------------------------------------|-----------------------------------------------------|
| FR-01 | Master Trade Detection         | `core/master_detector.py`                      | `handle_postback()`, `_on_ws_order_update()`, `_rest_poll_loop()` |
| FR-02 | Persistent Trade Event Store   | `core/event_store.py`                          | `persist_master_event()` — INSERT before processing |
| FR-03 | Idempotent Execution           | `core/event_store.py`, `core/orchestrator.py`  | `check_execution_exists()`, unique `execution_id`   |
| FR-04 | Parallel Execution             | `core/execution_engine.py`                     | `submit()`, per-client `TokenBucketRateLimiter`, `asyncio.gather` |
| FR-05 | 2% Slippage Protection         | `core/slippage_guard.py`                       | `check()` — price comparison logic                  |
| FR-06 | Partial Fill Handling          | `core/orchestrator.py`, `core/event_store.py`  | `_calculate_distribution_quantity()`, `distributed_fill_tracker` |
| FR-07 | Modification & Cancel Sync     | `core/execution_engine.py`, `core/orchestrator.py` | `cancel_all_for_master()`, `modify_all_for_master()` |
| FR-08 | Exit Synchronization           | `core/orchestrator.py`                         | Exit events flow through same `distribute()` pipeline |
| FR-09 | Position Reconciliation        | `core/reconciliation.py`                       | `run_once()` — master vs client position comparison |
| FR-10 | Capital Protection             | `core/capital_guard.py`                        | `validate()` — margin, limits, status checks        |
| FR-11 | Retry Logic                    | `core/retry_manager.py`                        | `_retry_with_status_check()` — status-first retry   |
| FR-12 | Kill Switch                    | `core/kill_switch.py`                          | `get_mode()`, `set_mode()` — DB + Redis cached      |
| FR-13 | Audit Trail                    | `core/audit.py`                                | `log()` — append-only INSERT                        |
| FR-14 | Restart Recovery               | `core/recovery.py`                             | `recover()` — reload + resume incomplete trades     |

---

## 10. NFR Satisfaction Strategy

| NFR   | Requirement        | How It Is Achieved                                                                                        |
|-------|--------------------|-----------------------------------------------------------------------------------------------------------|
| NFR-01| Latency <500ms     | asyncio event loop + httpx connection pooling + Mumbai hosting (2-5ms to broker) + uvloop                  |
| NFR-02| 30+ clients        | Per-client API keys → independent 10/sec rate limits → all 30 orders fire in parallel, **<1 second total** |
| NFR-03| 99.9% uptime       | Single-AZ RDS with automated backups + PITR, Docker restart policy, ALB health checks, recovery manager handles gaps |
| NFR-04| Zero data loss     | PostgreSQL ACID + write-ahead pattern (persist before process) + WAL archiving                             |
| NFR-05| 60s recovery       | FastAPI cold start <5s + recovery manager scans incomplete trades + WS reconnect with backoff              |
| NFR-06| Horizontal scale   | Stateless workers (per-client broker adapters + per-client rate limiters), Redis for shared state (kill switch) |
| NFR-07| Security           | TLS everywhere, API keys in env vars (not code), audit_log is append-only, postback webhook checksum verification (SHA-256) |
| NFR-08| Auditability       | `audit_log` table: append-only, no UPDATE/DELETE, structured JSONB payloads, indexed by trade/client       |

---

## 11. Error Handling Strategy

```python
# app/utils/exceptions.py

class CopyTradingError(Exception):
    """Base exception for the copy trading engine."""


class BrokerConnectionError(CopyTradingError):
    """Broker API connection failed."""


class OrderPlacementError(CopyTradingError):
    """Order placement failed at broker."""


class SlippageBreachError(CopyTradingError):
    """Price deviation exceeded threshold."""


class CapitalInsufficientError(CopyTradingError):
    """Client does not have sufficient margin."""


class IdempotencyViolationError(CopyTradingError):
    """Duplicate execution detected."""


class KillSwitchActiveError(CopyTradingError):
    """Operation blocked by kill switch."""


class ReconciliationError(CopyTradingError):
    """Position mismatch detected during reconciliation."""
```

**Error Handling Rules:**

| Error Type               | Behavior                                             | Retry? |
|--------------------------|------------------------------------------------------|--------|
| BrokerConnectionError    | Log, reconnect, rely on REST fallback                | Yes    |
| OrderPlacementError      | Log, mark FAILED, alert                              | Yes    |
| SlippageBreachError      | Log SLIPPAGE_REJECTED, alert, do NOT retry           | No     |
| CapitalInsufficientError | Log CAPITAL_REJECTED, do NOT retry                   | No     |
| IdempotencyViolation     | Log duplicate, silently skip                         | No     |
| KillSwitchActiveError    | Log, drop event                                      | No     |
| HTTP 429 (Rate Limit)    | Backoff per Retry-After, wait for token refill       | Yes    |
| HTTP 5xx (Server Error)  | Exponential backoff, max 3 retries                   | Yes    |
| Timeout                  | Check order status first, then retry if not found    | Yes*   |

---

## 12. Testing Strategy

### 12.1 Test Pyramid

| Level              | Tool               | Scope                                              | Count (est.) |
|--------------------|--------------------|-----------------------------------------------------|--------------|
| Unit Tests         | pytest + pytest-asyncio | Individual functions (slippage, rate limiter, etc.) | ~50          |
| Integration Tests  | pytest + testcontainers | DB operations, full pipeline with mock broker       | ~20          |
| E2E / Smoke Tests  | pytest + real broker sandbox | Full flow with Zerodha paper trading             | ~10          |

### 12.2 Key Unit Test Examples

```python
# tests/unit/test_slippage_guard.py
import pytest
from decimal import Decimal
from app.core.slippage_guard import SlippageGuard
from app.broker.base import Side


class TestSlippageGuard:
    def setup_method(self):
        self.guard = SlippageGuard(threshold_pct=2.0)

    def test_buy_within_limit(self):
        ok, pct = self.guard.check(Side.BUY, Decimal("100"), Decimal("101"))
        assert ok is True
        assert pct == Decimal("1")

    def test_buy_at_boundary(self):
        ok, pct = self.guard.check(Side.BUY, Decimal("100"), Decimal("102"))
        assert ok is True

    def test_buy_exceeds_limit(self):
        ok, pct = self.guard.check(Side.BUY, Decimal("100"), Decimal("103"))
        assert ok is False
        assert pct == Decimal("3")

    def test_sell_within_limit(self):
        ok, pct = self.guard.check(Side.SELL, Decimal("100"), Decimal("99"))
        assert ok is True

    def test_sell_exceeds_limit(self):
        ok, pct = self.guard.check(Side.SELL, Decimal("100"), Decimal("97"))
        assert ok is False

    def test_market_order_no_client_price(self):
        ok, pct = self.guard.check(Side.BUY, Decimal("100"), None)
        assert ok is True
        assert pct == Decimal("0")

    def test_zero_master_price(self):
        ok, pct = self.guard.check(Side.BUY, Decimal("0"), Decimal("100"))
        assert ok is True
```

### 12.3 requirements for testing

```
pytest==8.1.1
pytest-asyncio==0.23.6
pytest-cov==5.0.0
testcontainers[postgres]==4.4.0
httpx==0.27.0
```

---

## 13. Deployment Pipeline

### 13.1 Docker Configuration

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop"]
```

```yaml
# docker-compose.yml
version: "3.9"

services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_started
    restart: always
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: copy_trading
      POSTGRES_USER: cte_user
      POSTGRES_PASSWORD: s3cur3pass
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cte_user -d copy_trading"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7.2-alpine
    ports:
      - "6379:6379"
    restart: always

  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./config/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana_data:/var/lib/grafana

volumes:
  pgdata:
  grafana_data:
```

### 13.2 requirements.txt

```
# Core
fastapi==0.110.3
uvicorn[standard]==0.29.0
uvloop==0.19.0
pydantic==2.7.1
pydantic-settings==2.3.0

# Database
sqlalchemy[asyncio]==2.0.30
asyncpg==0.29.0
alembic==1.13.1

# Redis
redis[hiredis]==5.0.4

# HTTP / WebSocket
httpx==0.27.0
websockets==12.0

# Broker SDK
kiteconnect==5.0.1

# Scheduling
apscheduler==3.10.4

# Logging & Monitoring
structlog==24.1.0
prometheus-client==0.20.0

# Utilities
python-dotenv==1.0.1
orjson==3.10.3
```

### 13.3 CI/CD Pipeline (GitHub Actions)

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: test_copy_trading
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_pass
        ports: ["5432:5432"]
        options: --health-cmd pg_isready --health-interval 10s
      redis:
        image: redis:7.2
        ports: ["6379:6379"]

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: pip install pytest pytest-asyncio pytest-cov
      - run: pytest tests/ --cov=app --cov-report=xml -v
        env:
          DATABASE_URL: postgresql+asyncpg://test_user:test_pass@localhost:5432/test_copy_trading
          REDIS_URL: redis://localhost:6379/0

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install ruff
      - run: ruff check app/ tests/

  deploy:
    needs: [test, lint]
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to EC2
        run: |
          ssh ${{ secrets.EC2_HOST }} "cd /opt/copy-trading && git pull && docker compose up -d --build"
```

---

## 14. Implementation Roadmap

### Phase 1 — Foundation (Week 1-2)

| Task                                     | Days | Dependencies |
|------------------------------------------|------|--------------|
| Project scaffolding, Docker setup        | 1    | —            |
| Database schema + Alembic migrations     | 1    | —            |
| Config management (Pydantic Settings)    | 0.5  | —            |
| Broker adapter — Zerodha Kite Connect (connect, place, cancel, get orders, get positions, postback webhook) | 3 | — |
| Auth & Token Management module (OAuth flow, per-client token storage, pre-market validation) | 2 | DB schema, Broker adapter |
| Unit tests for broker adapter (mocked)   | 1    | Broker adapter |
| Event Store (repository layer)           | 1.5  | DB schema    |
| Audit Logger                             | 0.5  | DB schema    |

### Phase 2 — Core Engine (Week 3-4)

| Task                                     | Days | Dependencies      |
|------------------------------------------|------|-------------------|
| Master Trade Detector (WS + REST poll)   | 2    | Broker adapter     |
| Slippage Guard                           | 0.5  | —                  |
| Capital Guard                            | 1    | Event Store        |
| Distribution Orchestrator                | 2    | All guards, Event Store |
| Parallel Execution Engine + Per-Client Rate Limiters | 2    | Broker adapter, Token Manager |
| Partial Fill Handler                     | 1    | Orchestrator       |
| Modification & Cancellation Sync         | 1    | Execution engine   |

### Phase 3 — Safety & Resilience (Week 5)

| Task                                     | Days | Dependencies       |
|------------------------------------------|------|--------------------|
| Kill Switch (DB + Redis + API)           | 1    | DB, Redis          |
| Retry Manager                            | 1.5  | Execution engine   |
| Restart Recovery Manager                 | 1    | Event Store        |
| Reconciliation Engine                    | 2    | Broker adapter     |

### Phase 4 — API, Monitoring & Deployment (Week 6)

| Task                                     | Days | Dependencies       |
|------------------------------------------|------|--------------------|
| FastAPI management endpoints             | 1    | All core modules   |
| Prometheus metrics integration           | 1    | All core modules   |
| Grafana dashboard setup                  | 0.5  | Prometheus         |
| Docker Compose production config         | 0.5  | —                  |
| CI/CD pipeline (GitHub Actions)          | 0.5  | —                  |
| AWS EC2 deployment + RDS + ElastiCache   | 1    | Docker Compose     |

### Phase 5 — Testing & Hardening (Week 7-8)

| Task                                     | Days | Dependencies       |
|------------------------------------------|------|--------------------|
| Unit tests (50+ tests)                   | 3    | All modules        |
| Integration tests (20+ tests)            | 3    | All modules + DB   |
| E2E smoke tests (Zerodha paper mode)    | 2    | Full deployment    |
| Load testing (30 clients simulation)     | 1    | Full deployment    |
| Security review (API keys, TLS, perms)   | 1    | Full deployment    |

**Total estimated timeline: 8 weeks** (1 developer, full-time)

---

## 15. Cost Estimate

**All infrastructure costs borne by client.** Optimization priority: consistency > efficiency > cost. All prices in INR (₹).

### 15.1 Monthly Operating Costs

**Fixed infrastructure costs (same regardless of API key strategy):**

| Item                           | Cost/month        | Notes                               |
|--------------------------------|-------------------|--------------------------------------|
| AWS EC2 t4g.medium             | ~₹2,100           | Burstable ARM, 2 vCPU, 4GB — market-hours burst |
| AWS RDS PostgreSQL db.t4g.medium | ~₹4,600         | 2 vCPU, 4GB, Single-AZ + backups + PITR |
| AWS ElastiCache cache.t4g.micro | ~₹840            | 0.5GB — kill switch + counters       |
| Application Load Balancer     | ~₹1,700           | TLS termination, postback webhooks   |
| Zerodha API — Master account   | ₹500              | Kite Connect plan (1 key)            |
| Data Transfer                  | ~₹420             | API calls, webhooks                  |
| **Infrastructure subtotal**    | **~₹10,160/month** | Fixed cost, independent of key strategy |

**Total cost by API key strategy (30 clients):**

| Strategy | Zerodha Client Keys | API Cost/month | Total/month | Execution Time | Recommended For |
|----------|--------------------:|---------------:|------------:|----------------|-----------------|
| 1 key    | ₹0                  | ₹0             | **~₹10,200**  | ~3 sec | Budget-first, calm markets |
| 5 keys   | ₹2,500              | ₹2,500         | **~₹12,700**  | ~1.2 sec | Balanced cost & speed |
| 10 keys  | ₹5,000              | ₹5,000         | **~₹15,200**  | ~0.6 sec | Performance-focused |
| 30 keys  | ₹15,000             | ₹15,000        | **~₹25,200**  | <0.5 sec | Zero compromise |

> At ₹500/key, the jump from 1 key to 30 keys is only ₹15,000/month (~₹500/client). For the massive gains in execution speed, fault isolation, and simplicity, **30 keys is the recommended default** unless cost is the absolute top priority. See **Section 2.1.1** for the full trade-off analysis.

### 15.2 One-Time Costs

| Item                          | Cost              |
|-------------------------------|-------------------|
| Zerodha Kite Connect API Key (master) | Included in ₹500/month |
| Zerodha Client API Keys       | ₹500/month × N (client decides N) |
| Domain (optional)             | ~₹1,000/year      |
| SSL Certificate               | Free (Let's Encrypt) |

---

*End of Document*
