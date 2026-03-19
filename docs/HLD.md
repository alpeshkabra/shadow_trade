# High-Level Design (HLD)

## Copy Trading Engine — Swing Trading

| Field              | Detail                                      |
|--------------------|---------------------------------------------|
| **Document ID**    | HLD-CTE-2026-001                            |
| **Version**        | 1.0                                         |
| **Date**           | 18 March 2026                               |
| **Status**         | Draft                                       |
| **Related BRD**    | BRD-CTE-2026-001                            |
| **Classification** | Confidential                                |

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [System Context & Boundaries](#2-system-context--boundaries)
3. [Architecture Overview](#3-architecture-overview)
4. [Component Design](#4-component-design)
5. [Data Architecture](#5-data-architecture)
6. [Interaction Flows](#6-interaction-flows)
7. [Concurrency & Parallelism Model](#7-concurrency--parallelism-model)
8. [Resilience & Fault Tolerance](#8-resilience--fault-tolerance)
9. [Security Considerations](#9-security-considerations)
10. [Deployment Architecture](#10-deployment-architecture)
11. [Technology Stack (Recommended)](#11-technology-stack-recommended)
12. [API Design Overview](#12-api-design-overview)
13. [Monitoring & Observability](#13-monitoring--observability)
14. [Capacity Planning](#14-capacity-planning)
15. [Open Questions & Future Considerations](#15-open-questions--future-considerations)

---

## 1. Introduction

### 1.1 Purpose

This document presents the high-level design for the Copy Trading Engine (CTE). It translates the business requirements defined in BRD-CTE-2026-001 into an architectural blueprint covering system components, data flows, concurrency models, resilience patterns, and deployment topology.

### 1.2 Design Principles

| Principle                     | Description                                                                  |
|-------------------------------|------------------------------------------------------------------------------|
| **Event-Driven**              | All trade operations are triggered by events, not polling                    |
| **Persistence-First**         | Every event is persisted before downstream processing                        |
| **Idempotent by Design**      | Every operation is safe to retry without side effects                         |
| **Fail-Safe**                 | On ambiguity, the system halts rather than risking incorrect execution       |
| **Observable**                | Every operation emits structured logs and metrics                            |
| **Recoverable**               | System can reconstruct full state from the persistent store on restart       |

---

## 2. System Context & Boundaries

### 2.1 Context Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        BROKER PLATFORM                           │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────┐  │
│  │  WebSocket   │    │   REST API   │    │  Order Management  │  │
│  │  Feed        │    │   Gateway    │    │  System (OMS)      │  │
│  └──────┬──────┘    └──────┬───────┘    └────────────────────┘  │
└─────────┼──────────────────┼────────────────────────────────────┘
          │                  │
          ▼                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                    COPY TRADING ENGINE                            │
│                                                                  │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────┐   │
│  │ Master Trade   │  │ Trade Event    │  │ Execution        │   │
│  │ Detector       │──│ Store          │──│ Engine           │   │
│  └────────────────┘  └────────────────┘  └──────────────────┘   │
│                                                                  │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────┐   │
│  │ Reconciliation │  │ Kill Switch    │  │ Audit Logger     │   │
│  │ Engine         │  │ Controller     │  │                  │   │
│  └────────────────┘  └────────────────┘  └──────────────────┘   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────────────┐
│                    DATA & INFRASTRUCTURE                          │
│  ┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌──────────┐  │
│  │ Database │  │ Message Queue│  │ Alert Svc  │  │ Metrics  │  │
│  └──────────┘  └──────────────┘  └────────────┘  └──────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 External Interfaces

| Interface               | Protocol     | Direction | Purpose                                    |
|-------------------------|--------------|-----------|--------------------------------------------|
| Broker WebSocket Feed   | WSS          | Inbound   | Real-time order event stream               |
| Broker REST API         | HTTPS        | Outbound  | Order placement, status query, positions   |
| Database                | TCP          | Bidirect  | Persistent trade event store               |
| Alert Service           | HTTPS/SMTP   | Outbound  | Slippage alerts, system alerts             |
| Monitoring              | HTTPS        | Outbound  | Metrics, health checks                     |

---

## 3. Architecture Overview

### 3.1 Architectural Style

The system follows an **event-driven, microkernel architecture** with a central persistent event store. Components communicate via in-process events and a shared database. The design favors a monolithic deployment for simplicity and low latency, with clear internal module boundaries that allow future decomposition into microservices if needed.

### 3.2 High-Level Component Diagram

```
                          ┌─────────────────────┐
                          │   Kill Switch        │
                          │   Controller         │
                          └──────────┬──────────┘
                                     │ (gates all operations)
                                     ▼
┌─────────────┐    ┌──────────────────────────────────┐    ┌──────────────┐
│  Broker WS  │───▶│     Master Trade Detector         │    │  Broker REST │
│  Feed       │    │  ┌────────────┐ ┌──────────────┐ │◀───│  Reconciler  │
└─────────────┘    │  │ WS Listener│ │REST Reconciler│ │    └──────────────┘
                   │  └─────┬──────┘ └──────┬───────┘ │
                   └────────┼───────────────┼─────────┘
                            │               │
                            ▼               ▼
                   ┌─────────────────────────────────┐
                   │    Trade Event Store (DB)        │
                   │  ┌───────────────────────────┐  │
                   │  │  master_trade_events       │  │
                   │  │  client_executions         │  │
                   │  │  reconciliation_snapshots  │  │
                   │  └───────────────────────────┘  │
                   └──────────────┬──────────────────┘
                                  │
                                  ▼
                   ┌─────────────────────────────────┐
                   │    Distribution Orchestrator     │
                   │  ┌─────────┐ ┌───────────────┐  │
                   │  │Slippage │ │Capital/Margin  │  │
                   │  │Guard    │ │Validator       │  │
                   │  └─────────┘ └───────────────┘  │
                   └──────────────┬──────────────────┘
                                  │
                                  ▼
                   ┌─────────────────────────────────┐
                   │    Parallel Execution Engine     │
                   │  ┌──────┐┌──────┐┌──────┐       │
                   │  │Wkr 1 ││Wkr 2 ││Wkr N │       │
                   │  └──┬───┘└──┬───┘└──┬───┘       │
                   │     │       │       │            │
                   │  ┌──▼───────▼───────▼──┐        │
                   │  │   Rate Limiter       │        │
                   │  │   (Token Bucket)     │        │
                   │  └──────────┬───────────┘        │
                   └─────────────┼────────────────────┘
                                 │
                                 ▼
                          ┌──────────────┐
                          │  Broker REST  │
                          │  API (Orders) │
                          └──────────────┘
```

---

## 4. Component Design

### 4.1 Master Trade Detector

**Responsibility:** Capture all order lifecycle events from the master account.

| Sub-Component        | Description                                                               |
|----------------------|---------------------------------------------------------------------------|
| WebSocket Listener   | Maintains a persistent WSS connection to the broker's order feed          |
| REST Reconciler      | Polls broker REST API every 30 seconds as a fallback                      |
| Event Deduplicator   | Deduplicates events from WS and REST to prevent double-processing         |
| Connection Manager   | Handles WS reconnection with exponential backoff                          |

**Behavioral Rules:**
- On WS event received → deduplicate → persist to Trade Event Store → emit internal event
- On REST poll → compare with persisted state → persist any new events → emit internal events
- On WS disconnect → log, attempt reconnect, rely on REST reconciler until WS restores

**State Machine — WebSocket Connection:**

```
                    ┌───────────┐
         ┌────────▶│ CONNECTED  │◀────────┐
         │         └─────┬─────┘          │
    reconnect            │ disconnect     │ connect
    success              ▼                │
         │         ┌───────────┐          │
         └─────────│RECONNECTING│─────────┘
                   └─────┬─────┘
                         │ max retries exceeded
                         ▼
                   ┌───────────┐
                   │  FAILED   │──▶ Alert + REST-only mode
                   └───────────┘
```

---

### 4.2 Trade Event Store

**Responsibility:** Durable, non-volatile storage of all trade events and execution states.

**Design Decisions:**
- Write-ahead: every event persisted before any downstream action
- Append-only for audit: trade events are never updated, only new status records appended
- Client execution records track the full lifecycle per client per master trade

**Key Tables (see Section 5 for full schema):**
- `master_trade_events` — raw master events
- `client_execution_log` — per-client execution tracking
- `reconciliation_snapshots` — periodic position snapshots

---

### 4.3 Distribution Orchestrator

**Responsibility:** For each master trade event, coordinate distribution to all eligible client accounts.

**Sub-Components:**

| Sub-Component         | Description                                                              |
|-----------------------|--------------------------------------------------------------------------|
| Slippage Guard        | Validates price deviation < 2% before execution                          |
| Capital Validator     | Checks margin, open trade limits, account status                         |
| Idempotency Checker   | Verifies no prior execution exists for this client + master_trade_id     |
| Fill Tracker          | Tracks partial fill state and distributes only incremental fills         |

**Processing Pipeline per Master Event:**

```
Master Event Received
        │
        ▼
┌─────────────────┐    NO     ┌──────────────────┐
│  Kill Switch    │──────────▶│  DROP EVENT       │
│  Active?        │           │  (Log & Alert)    │
└────────┬────────┘           └──────────────────┘
         │ YES (system active)
         ▼
┌─────────────────┐
│  For each       │
│  client account │
└────────┬────────┘
         │
         ▼
┌─────────────────┐    ALREADY    ┌──────────────────┐
│  Idempotency    │──────────────▶│  SKIP            │
│  Check          │  EXECUTED     │  (Log duplicate)  │
└────────┬────────┘               └──────────────────┘
         │ NOT EXECUTED
         ▼
┌─────────────────┐    FAIL    ┌──────────────────┐
│  Capital &      │───────────▶│  REJECT          │
│  Margin Check   │            │  (Insufficient)  │
└────────┬────────┘            └──────────────────┘
         │ PASS
         ▼
┌─────────────────┐    BREACH  ┌──────────────────┐
│  Slippage       │───────────▶│  REJECT          │
│  Check (2%)     │            │  (SLIPPAGE_REJ)  │
└────────┬────────┘            └──────────────────┘
         │ WITHIN LIMIT
         ▼
┌─────────────────┐
│  Submit to      │
│  Execution Pool │
└─────────────────┘
```

---

### 4.4 Parallel Execution Engine

**Responsibility:** Execute client orders concurrently while respecting broker rate limits.

**Design:**

| Aspect              | Implementation                                                            |
|---------------------|---------------------------------------------------------------------------|
| Concurrency Model   | Async worker pool (configurable pool size, default: 10 workers)           |
| Rate Limiting       | Token bucket algorithm (e.g., 10 requests/second, configurable)           |
| Backpressure        | When rate limit hit, queue orders internally; resume on token refill      |
| Order Placement     | Each worker: acquire token → place order via REST → record result         |
| Timeout             | Per-order timeout (e.g., 5 seconds); on timeout, enter retry path        |

**Worker Lifecycle:**

```
┌───────────┐     ┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│  Receive   │────▶│  Acquire     │────▶│  Place Order   │────▶│  Record      │
│  Task      │     │  Rate Token  │     │  (Broker API)  │     │  Result      │
└───────────┘     └──────────────┘     └───────┬───────┘     └──────────────┘
                                               │
                                    ┌──────────┼──────────┐
                                    ▼          ▼          ▼
                               ┌────────┐ ┌────────┐ ┌────────┐
                               │SUCCESS │ │TIMEOUT │ │REJECTED│
                               └────────┘ └───┬────┘ └────────┘
                                              │
                                              ▼
                                    ┌──────────────┐
                                    │  Retry Path  │
                                    │  (FR-11)     │
                                    └──────────────┘
```

---

### 4.5 Reconciliation Engine

**Responsibility:** Periodic verification that client positions match master positions.

**Design:**

| Aspect              | Implementation                                                            |
|---------------------|---------------------------------------------------------------------------|
| Trigger             | Scheduled every 60 seconds (configurable)                                 |
| Data Source          | Broker REST API for master and all client positions                      |
| Comparison           | Instrument-wise net quantity comparison                                  |
| On Mismatch          | Log, alert, optionally auto-correct or disable client                   |

**Reconciliation Flow:**

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Fetch Master   │────▶│  Fetch All       │────▶│  Compare         │
│  Positions      │     │  Client Positions│     │  Instrument-wise │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                                               ┌──────────┼──────────┐
                                               ▼                     ▼
                                         ┌──────────┐         ┌──────────────┐
                                         │  MATCH   │         │  MISMATCH    │
                                         │  (Log OK)│         │              │
                                         └──────────┘         └──────┬───────┘
                                                                     │
                                                          ┌──────────┼──────────┐
                                                          ▼                     ▼
                                                   ┌────────────┐       ┌────────────┐
                                                   │Auto-Correct│       │Disable     │
                                                   │(if enabled)│       │Client      │
                                                   └────────────┘       └────────────┘
```

---

### 4.6 Kill Switch Controller

**Responsibility:** Emergency control to halt or restrict trading operations.

**Design:**
- Backed by a runtime-configurable flag (database or in-memory with persistence)
- All components check kill switch state before processing
- Two modes:
  - `STOP_NEW_ENTRIES` — blocks new order placements; allows exit/SL/target orders
  - `FULL_HALT` — blocks all order operations
- Activatable via API endpoint or manual database flag (no deployment required)

---

### 4.7 Audit Logger

**Responsibility:** Immutable, structured logging of all trade-related actions.

**Design:**
- Append-only log table in database (no UPDATE/DELETE operations)
- Each log entry includes: `event_id`, `timestamp`, `component`, `event_type`, `master_trade_id`, `client_account_id`, `payload`, `outcome`
- Separate from application logs — this is the compliance/audit trail
- Retention policy configurable per regulatory requirement

---

## 5. Data Architecture

### 5.1 Entity Relationship Overview

```
┌─────────────────────┐        ┌───────────────────────┐
│  master_trade_events │        │  client_accounts       │
├─────────────────────┤        ├───────────────────────┤
│  id (PK)            │        │  id (PK)              │
│  master_trade_id    │◀──┐    │  account_id           │
│  event_type         │   │    │  broker_account_id    │
│  instrument         │   │    │  status (ACTIVE/      │
│  side               │   │    │         DISABLED/     │
│  quantity           │   │    │         BLOCKED)      │
│  order_type         │   │    │  max_open_trades      │
│  price              │   │    │  created_at           │
│  filled_quantity    │   │    └───────────┬───────────┘
│  status             │   │                │
│  timestamp          │   │                │
│  raw_payload        │   │                │
└─────────────────────┘   │                │
                          │                │
┌─────────────────────────┼────────────────┼───────────┐
│  client_execution_log   │                │           │
├─────────────────────────┤                │           │
│  id (PK)                │                │           │
│  execution_id (UNIQUE)  │  ◀─────────────┘           │
│  master_trade_id (FK)   │──┘                         │
│  client_account_id (FK) │────────────────────────────┘
│  instrument             │
│  side                   │
│  quantity               │
│  order_type             │
│  price                  │
│  broker_order_id        │
│  status                 │
│  slippage_pct           │
│  retry_count            │
│  created_at             │
│  updated_at             │
└─────────────────────────┘

┌─────────────────────────┐    ┌───────────────────────────┐
│  reconciliation_snapshots│    │  audit_log                │
├─────────────────────────┤    ├───────────────────────────┤
│  id (PK)                │    │  id (PK)                  │
│  snapshot_time          │    │  event_id (UNIQUE)        │
│  account_id             │    │  timestamp                │
│  account_type           │    │  component                │
│  instrument             │    │  event_type               │
│  net_quantity           │    │  master_trade_id          │
│  match_status           │    │  client_account_id        │
│  discrepancy_detail     │    │  payload (JSONB)          │
└─────────────────────────┘    │  outcome                  │
                               └───────────────────────────┘
```

### 5.2 Key Table Definitions

#### `master_trade_events`

| Column           | Type         | Constraints          | Description                          |
|------------------|--------------|----------------------|--------------------------------------|
| id               | BIGSERIAL    | PK                   | Auto-increment primary key           |
| master_trade_id  | VARCHAR(64)  | NOT NULL, INDEXED    | Broker-assigned order ID             |
| event_type       | VARCHAR(32)  | NOT NULL             | NEW, MODIFY, CANCEL, PARTIAL_FILL, FULL_FILL, REJECTION |
| instrument       | VARCHAR(32)  | NOT NULL             | Trading symbol                       |
| side             | VARCHAR(4)   | NOT NULL             | BUY or SELL                          |
| quantity         | DECIMAL(18,4)| NOT NULL             | Order quantity                       |
| order_type       | VARCHAR(16)  | NOT NULL             | MARKET, LIMIT, SL, SL-M             |
| price            | DECIMAL(18,4)| NULLABLE             | Order/trigger price                  |
| filled_quantity  | DECIMAL(18,4)| DEFAULT 0            | Cumulative filled quantity           |
| status           | VARCHAR(32)  | NOT NULL             | PENDING, OPEN, PARTIAL, FILLED, CANCELLED, REJECTED |
| timestamp        | TIMESTAMP    | NOT NULL             | Event timestamp (UTC, ms precision)  |
| raw_payload      | JSONB        | NULLABLE             | Full broker response for audit       |
| created_at       | TIMESTAMP    | DEFAULT NOW()        | Record creation time                 |

#### `client_execution_log`

| Column            | Type         | Constraints                         | Description                          |
|-------------------|--------------|-------------------------------------|--------------------------------------|
| id                | BIGSERIAL    | PK                                  | Auto-increment primary key           |
| execution_id      | VARCHAR(128) | UNIQUE, NOT NULL                    | `{client_account_id}:{master_trade_id}:{event_seq}` |
| master_trade_id   | VARCHAR(64)  | FK → master_trade_events, NOT NULL  | Reference to master event            |
| client_account_id | VARCHAR(64)  | FK → client_accounts, NOT NULL      | Client account identifier            |
| instrument        | VARCHAR(32)  | NOT NULL                            | Trading symbol                       |
| side              | VARCHAR(4)   | NOT NULL                            | BUY or SELL                          |
| quantity          | DECIMAL(18,4)| NOT NULL                            | Quantity to execute                  |
| order_type        | VARCHAR(16)  | NOT NULL                            | MARKET, LIMIT, SL, SL-M             |
| price             | DECIMAL(18,4)| NULLABLE                            | Execution price                      |
| broker_order_id   | VARCHAR(64)  | NULLABLE                            | Broker-assigned order ID for client  |
| status            | VARCHAR(32)  | NOT NULL                            | PENDING, SUBMITTED, FILLED, REJECTED, SLIPPAGE_REJECTED, FAILED |
| slippage_pct      | DECIMAL(8,4) | NULLABLE                            | Calculated slippage percentage       |
| retry_count       | INT          | DEFAULT 0                           | Number of retries attempted          |
| error_detail      | TEXT         | NULLABLE                            | Error message if failed              |
| created_at        | TIMESTAMP    | DEFAULT NOW()                       | Record creation time                 |
| updated_at        | TIMESTAMP    | DEFAULT NOW()                       | Last status update time              |

#### `audit_log`

| Column            | Type         | Constraints          | Description                          |
|-------------------|--------------|----------------------|--------------------------------------|
| id                | BIGSERIAL    | PK                   | Auto-increment primary key           |
| event_id          | VARCHAR(128) | UNIQUE, NOT NULL     | Globally unique event identifier     |
| timestamp         | TIMESTAMP    | NOT NULL             | Event timestamp (UTC)                |
| component         | VARCHAR(64)  | NOT NULL             | Source component name                |
| event_type        | VARCHAR(64)  | NOT NULL             | Event classification                 |
| master_trade_id   | VARCHAR(64)  | NULLABLE             | Associated master trade              |
| client_account_id | VARCHAR(64)  | NULLABLE             | Associated client account            |
| payload           | JSONB        | NOT NULL             | Full event payload                   |
| outcome           | VARCHAR(32)  | NOT NULL             | SUCCESS, FAILURE, SKIPPED, REJECTED  |

### 5.3 Indexing Strategy

| Table                    | Index                                              | Purpose                              |
|--------------------------|----------------------------------------------------|--------------------------------------|
| master_trade_events      | `idx_mte_master_trade_id` on (master_trade_id)     | Lookup by master trade               |
| master_trade_events      | `idx_mte_status` on (status)                       | Find incomplete trades on restart    |
| client_execution_log     | `idx_cel_execution_id` UNIQUE on (execution_id)    | Idempotency enforcement              |
| client_execution_log     | `idx_cel_master_client` on (master_trade_id, client_account_id) | Distribution status lookup |
| client_execution_log     | `idx_cel_status` on (status)                       | Find pending executions on restart   |
| audit_log                | `idx_al_master_trade` on (master_trade_id)         | Audit trail per trade                |
| audit_log                | `idx_al_client` on (client_account_id)             | Audit trail per client               |

---

## 6. Interaction Flows

### 6.1 New Order Flow (Happy Path)

```
Broker WS ──▶ Master Trade Detector
                    │
                    │ 1. Receive NEW order event
                    ▼
              Trade Event Store
                    │
                    │ 2. Persist master_trade_event (status=PENDING)
                    ▼
           Distribution Orchestrator
                    │
                    │ 3. For each client account:
                    │    a. Idempotency check  → PASS
                    │    b. Capital check      → PASS
                    │    c. Slippage check     → PASS (or N/A for LIMIT)
                    ▼
           Parallel Execution Engine
                    │
                    │ 4. Acquire rate token
                    │ 5. Place order via Broker REST API
                    │ 6. Record broker_order_id and status
                    ▼
              Trade Event Store
                    │
                    │ 7. Update client_execution_log (status=FILLED)
                    ▼
                Audit Logger
                    │
                    │ 8. Log completion event
                    ▼
                  DONE
```

### 6.2 Partial Fill Flow

```
Broker WS ──▶ Master Trade Detector
                    │
                    │ 1. Receive PARTIAL_FILL event (filled_qty: 50 of 100)
                    ▼
              Trade Event Store
                    │
                    │ 2. Update master_trade_event (filled_quantity=50)
                    ▼
           Distribution Orchestrator
                    │
                    │ 3. Calculate incremental fill:
                    │    new_fill = 50 - previous_distributed_qty (0) = 50
                    │
                    │ 4. For each client: distribute qty=50
                    │    (same pipeline: idempotency, capital, slippage)
                    ▼
           Parallel Execution Engine
                    │
                    │ 5. Execute incremental fill orders
                    ▼
                  DONE

... Later, another PARTIAL_FILL (filled_qty: 80 of 100) ...

           Distribution Orchestrator
                    │
                    │ Calculate incremental fill:
                    │ new_fill = 80 - 50 = 30
                    │ Distribute qty=30
                    ▼
                  DONE
```

### 6.3 Order Modification Flow

```
Broker WS ──▶ Master Trade Detector
                    │
                    │ 1. Receive MODIFY event (price changed)
                    ▼
              Trade Event Store
                    │
                    │ 2. Persist modification event
                    ▼
           Distribution Orchestrator
                    │
                    │ 3. For each client with active corresponding order:
                    │    a. Look up broker_order_id for this client
                    │    b. Submit modification via Broker REST API
                    │    c. Record result
                    ▼
                  DONE
```

### 6.4 Cancellation Flow

```
Broker WS ──▶ Master Trade Detector
                    │
                    │ 1. Receive CANCEL event
                    ▼
              Trade Event Store
                    │
                    │ 2. Persist cancellation event
                    ▼
           Distribution Orchestrator
                    │
                    │ 3. For each client with active corresponding order:
                    │    a. Submit cancellation via Broker REST API
                    │    b. Verify cancellation confirmed
                    │    c. Record result
                    ▼
                  DONE
```

### 6.5 Restart Recovery Flow

```
System Restart
        │
        ▼
┌─────────────────────┐
│ Load Configuration  │
│ Reconnect DB        │
└────────┬────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ Query incomplete master events  │
│ (status IN PENDING, OPEN,      │
│  PARTIAL)                       │
└────────┬────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ For each incomplete event:      │
│  Query client_execution_log     │
│  Identify undistributed clients │
│  Resume distribution pipeline   │
└────────┬────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│ Reconnect WebSocket             │
│ Start REST Reconciler           │
│ Start Reconciliation Engine     │
└─────────────────────────────────┘
         │
         ▼
    SYSTEM READY
```

---

## 7. Concurrency & Parallelism Model

### 7.1 Threading / Async Model

| Component                | Model                           | Rationale                                  |
|--------------------------|---------------------------------|--------------------------------------------|
| WebSocket Listener       | Single async connection         | One connection per master account           |
| REST Reconciler          | Scheduled task (cron-like)      | Periodic, non-blocking                     |
| Distribution Orchestrator| Event-driven, single-threaded   | Ensures ordering per master trade           |
| Execution Engine         | Async worker pool (N workers)   | Parallel client order placement             |
| Reconciliation Engine    | Scheduled task                  | Periodic, independent                      |

### 7.2 Rate Limiting

**Algorithm:** Token Bucket

| Parameter              | Default Value     | Configurable |
|------------------------|-------------------|--------------|
| Bucket capacity        | 10 tokens         | Yes          |
| Refill rate            | 10 tokens/second  | Yes          |
| Max burst              | 10                | Yes          |

**Behavior:**
- Worker acquires a token before each broker API call
- If no token available, worker waits (backpressure)
- Prevents broker API rate limit violations

### 7.3 Ordering Guarantees

- Events for the same `master_trade_id` are processed sequentially (FIFO)
- Events for different master trades may be processed concurrently
- Client executions for the same master event are parallelized across accounts

---

## 8. Resilience & Fault Tolerance

### 8.1 Failure Modes & Recovery

| Failure Mode                          | Detection                        | Recovery                                                    |
|---------------------------------------|----------------------------------|-------------------------------------------------------------|
| WebSocket disconnection               | Heartbeat timeout                | Auto-reconnect with exponential backoff; REST fallback      |
| Broker REST API timeout               | HTTP timeout (5s)                | Retry with status check (max 3 attempts)                    |
| Broker REST API rate limit (429)      | HTTP 429 response                | Backoff per Retry-After header; token bucket adjustment     |
| Database connection lost              | Connection pool health check     | Reconnect; halt new processing until restored               |
| Server crash                          | Process termination              | Restart recovery flow (Section 6.5)                         |
| Duplicate event from WS + REST        | Event deduplicator               | Idempotency check drops duplicate                           |
| Client order placement ambiguous      | Timeout without confirmation     | Status check → retry only if no order found                 |
| Slippage breach                       | Price comparison                 | Reject order, log, alert                                    |
| Client margin insufficient            | Pre-execution check              | Reject trade for that client                                |
| Position mismatch detected            | Reconciliation engine            | Log, alert, auto-correct or disable client                  |

### 8.2 Retry Policy

| Parameter           | Value                              |
|---------------------|------------------------------------|
| Max retries         | 3                                  |
| Backoff strategy    | Exponential (1s, 2s, 4s)          |
| Pre-retry check     | Verify order status before retry   |
| Idempotency         | Same execution_id on retry         |
| On max retries      | Mark as FAILED, alert, log         |

### 8.3 Circuit Breaker (Optional Enhancement)

For broker API calls, a circuit breaker pattern can be layered:

| State   | Behavior                                                      |
|---------|---------------------------------------------------------------|
| CLOSED  | Normal operation; track failure count                         |
| OPEN    | All calls fail-fast; alert raised; auto-reset after cooldown  |
| HALF    | Allow limited test calls; if successful, close circuit        |

---

## 9. Security Considerations

| Area                    | Measure                                                              |
|-------------------------|----------------------------------------------------------------------|
| API Credentials         | Encrypted at rest (AES-256); loaded from vault/env at startup        |
| Broker Communication    | TLS 1.2+ for all WebSocket and REST connections                      |
| Database Access         | Connection-level authentication; encrypted connections               |
| Audit Log Integrity     | Append-only table; no UPDATE/DELETE permissions for application user  |
| Kill Switch Access      | Restricted to admin role; audit-logged                               |
| Client Account Data     | Minimal PII; account IDs are broker references only                  |
| Network                 | System deployed in private network; broker APIs accessed via egress   |

---

## 10. Deployment Architecture

### 10.1 Deployment Topology

```
┌──────────────────────────────────────────────────────┐
│                  Private Network                      │
│                                                      │
│  ┌──────────────────────┐   ┌──────────────────┐    │
│  │  Copy Trading Engine │   │  PostgreSQL DB   │    │
│  │  (Application Server)│──▶│  (Primary)       │    │
│  │                      │   └────────┬─────────┘    │
│  │  • Master Detector   │            │               │
│  │  • Event Store       │   ┌────────▼─────────┐    │
│  │  • Orchestrator      │   │  PostgreSQL DB   │    │
│  │  • Execution Engine  │   │  (Read Replica)  │    │
│  │  • Reconciliation    │   └──────────────────┘    │
│  │  • Kill Switch       │                            │
│  │  • Audit Logger      │   ┌──────────────────┐    │
│  └──────────┬───────────┘   │  Redis (optional)│    │
│             │               │  (Rate limiter   │    │
│             │               │   state, caching)│    │
│             │               └──────────────────┘    │
│             │                                        │
│  ┌──────────▼───────────┐                            │
│  │  Monitoring Stack    │                            │
│  │  (Prometheus/Grafana │                            │
│  │   or equivalent)     │                            │
│  └──────────────────────┘                            │
└──────────────────────────────────────────────────────┘
             │
             │ TLS (Egress)
             ▼
┌──────────────────────┐
│   Broker APIs        │
│   (WSS + HTTPS)      │
└──────────────────────┘
```

### 10.2 Deployment Notes

- **Single-instance deployment** for Phase 1 (swing trading does not demand sub-millisecond latency)
- **Database:** PostgreSQL recommended (strong ACID guarantees, JSONB support, mature ecosystem)
- **Optional Redis:** For distributed rate limiter state if scaling to multiple instances later
- **Container-ready:** Dockerized application with health checks for orchestration readiness

---

## 11. Technology Stack (Recommended)

| Layer              | Technology                  | Rationale                                             |
|--------------------|-----------------------------|-------------------------------------------------------|
| Language           | Python 3.11+ or Java 17+   | Python for rapid development; Java for performance    |
| Async Framework    | asyncio (Python) / Virtual Threads (Java) | Non-blocking I/O for WebSocket and HTTP      |
| WebSocket Client   | websockets (Python) / OkHttp (Java)       | Mature, well-tested libraries               |
| HTTP Client        | httpx (Python) / OkHttp (Java)            | Async HTTP with connection pooling           |
| Database           | PostgreSQL 15+              | ACID, JSONB, strong indexing, mature ecosystem        |
| ORM / Query        | SQLAlchemy (Python) / JOOQ (Java)         | Type-safe queries, migration support         |
| Rate Limiter       | In-process token bucket     | Low latency; Redis-backed if multi-instance           |
| Scheduling         | APScheduler (Python) / ScheduledExecutor (Java) | Cron-like scheduling for reconciliation |
| Configuration      | Environment variables + YAML| Twelve-factor app compliance                          |
| Logging            | structlog (Python) / Logback (Java)       | Structured JSON logging                      |
| Monitoring         | Prometheus + Grafana        | Industry standard metrics and dashboards              |
| Containerization   | Docker                      | Reproducible deployments                              |
| Orchestration      | Docker Compose / K8s        | Compose for dev/staging; K8s for production (future)  |

---

## 12. API Design Overview

### 12.1 Internal Management APIs

These are operational APIs for system management (not client-facing).

| Endpoint                        | Method | Description                                      |
|---------------------------------|--------|--------------------------------------------------|
| `/api/v1/health`                | GET    | Health check (DB, WS connection, components)     |
| `/api/v1/kill-switch`           | GET    | Current kill switch state                        |
| `/api/v1/kill-switch`           | PUT    | Activate/deactivate kill switch (mode parameter) |
| `/api/v1/clients`               | GET    | List all client accounts and their status        |
| `/api/v1/clients/{id}/disable`  | PUT    | Disable a specific client account                |
| `/api/v1/clients/{id}/enable`   | PUT    | Re-enable a specific client account              |
| `/api/v1/reconciliation/status` | GET    | Latest reconciliation results                    |
| `/api/v1/reconciliation/run`    | POST   | Trigger manual reconciliation                    |
| `/api/v1/trades/active`         | GET    | List active/incomplete master trades             |
| `/api/v1/audit/{master_trade_id}`| GET   | Full audit trail for a master trade              |

### 12.2 Kill Switch API Detail

**PUT `/api/v1/kill-switch`**

Request:
```json
{
  "mode": "STOP_NEW_ENTRIES" | "FULL_HALT" | "DISABLED",
  "reason": "string (required)"
}
```

Response:
```json
{
  "previous_mode": "DISABLED",
  "current_mode": "FULL_HALT",
  "activated_at": "2026-03-18T10:30:00.000Z",
  "activated_by": "admin",
  "reason": "Market crash - emergency halt"
}
```

---

## 13. Monitoring & Observability

### 13.1 Key Metrics

| Metric                                  | Type      | Alert Threshold                    |
|-----------------------------------------|-----------|------------------------------------|
| `master_events_received_total`          | Counter   | —                                  |
| `master_events_missed`                  | Counter   | > 0 → Critical alert              |
| `client_executions_total`               | Counter   | —                                  |
| `client_executions_failed`              | Counter   | > 5 in 5 min → Warning            |
| `client_executions_duplicate_blocked`   | Counter   | > 0 → Info (expected on retry)     |
| `slippage_rejections_total`             | Counter   | > 3 in 5 min → Warning            |
| `execution_latency_ms`                  | Histogram | p99 > 2000ms → Warning            |
| `reconciliation_mismatches`             | Counter   | > 0 → Critical alert              |
| `ws_connection_status`                  | Gauge     | 0 (disconnected) → Critical alert |
| `rate_limiter_wait_time_ms`             | Histogram | p99 > 5000ms → Warning            |
| `retry_count_total`                     | Counter   | > 10 in 5 min → Warning           |
| `kill_switch_active`                    | Gauge     | 1 → Info notification             |
| `client_accounts_disabled`              | Gauge     | > 0 → Warning                     |

### 13.2 Health Check

**GET `/api/v1/health`**

```json
{
  "status": "healthy",
  "components": {
    "database": "connected",
    "websocket": "connected",
    "reconciliation_engine": "running",
    "kill_switch": "DISABLED",
    "last_master_event": "2026-03-18T10:29:58.123Z",
    "active_client_accounts": 28,
    "disabled_client_accounts": 2
  }
}
```

### 13.3 Alerting Strategy

| Severity  | Examples                                             | Channel              |
|-----------|------------------------------------------------------|----------------------|
| Critical  | WS disconnected > 60s, Reconciliation mismatch, Missed master event | SMS + PagerDuty |
| Warning   | High slippage rejections, Execution failures, Rate limiter saturation | Slack/Email    |
| Info      | Kill switch activated, Client disabled, System restart | Slack                |

---

## 14. Capacity Planning

### 14.1 Sizing Estimates

| Parameter                              | Estimate                         |
|----------------------------------------|----------------------------------|
| Master trades per day (swing)          | 5–20                             |
| Client accounts                        | 30 (initial), scalable to 100+  |
| Orders per master trade                | 30 (1 per client)               |
| Total client orders per day            | 150–600                          |
| Events per master trade (lifecycle)    | ~5 (new, partial, fill, modify, cancel) |
| Database writes per day                | ~5,000–20,000                    |
| Database storage per month             | ~50–200 MB                       |
| WebSocket messages per day             | ~100–500                         |

### 14.2 Resource Requirements (Phase 1)

| Resource           | Specification                              |
|--------------------|--------------------------------------------|
| Application Server | 2 vCPU, 4 GB RAM                           |
| Database           | 2 vCPU, 4 GB RAM, 50 GB SSD               |
| Network            | Standard bandwidth; low-latency to broker  |

---

## 15. Open Questions & Future Considerations

### 15.1 Open Questions

| ID    | Question                                                                                | Owner         |
|-------|-----------------------------------------------------------------------------------------|---------------|
| OQ-01 | Which broker(s) will be supported in Phase 1? (API specifics affect adapter design)     | Product Owner |
| OQ-02 | Should auto-correction on reconciliation mismatch be enabled by default?                | Product Owner |
| OQ-03 | What is the exact client account onboarding flow? (API key provisioning, etc.)          | Product Owner |
| OQ-04 | Is there a requirement for multi-master support in future phases?                       | Product Owner |
| OQ-05 | What regulatory framework applies? (Impacts audit log retention and encryption)         | Compliance    |
| OQ-06 | Should the 2% slippage threshold be configurable per client or per instrument?          | Product Owner |

### 15.2 Future Enhancements (Not in Scope)

| Enhancement                     | Description                                                     |
|---------------------------------|-----------------------------------------------------------------|
| Dashboard & Monitoring UI       | Web-based dashboard for trade monitoring and management         |
| Capital Scaling / Multiplier    | Proportional position sizing based on client capital            |
| Multi-Broker Support            | Abstract broker adapter layer for multiple broker integrations  |
| Performance Analytics           | P&L tracking, drawdown, Sharpe ratio per account                |
| Client Self-Service Portal      | Client-facing portal for viewing trades and managing settings   |
| Multi-Master Support            | Multiple master accounts with independent follower groups       |
| Notification System             | Client notifications for trades, alerts, and reports            |

---

*End of Document*
