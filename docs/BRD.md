# Business Requirements Document (BRD)

## Copy Trading Engine — Swing Trading


| Field              | Detail           |
| ------------------ | ---------------- |
| **Document ID**    | BRD-CTE-2026-001 |
| **Version**        | 1.0              |
| **Date**           | 18 March 2026    |
| **Status**         | Draft            |
| **Classification** | Confidential     |


---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Business Objectives](#2-business-objectives)
3. [Scope](#3-scope)
4. [Stakeholders](#4-stakeholders)
5. [Functional Requirements](#5-functional-requirements)
6. [Non-Functional Requirements](#6-non-functional-requirements)
7. [Business Rules & Constraints](#7-business-rules--constraints)
8. [Acceptance Criteria Summary](#8-acceptance-criteria-summary)
9. [Assumptions & Dependencies](#9-assumptions--dependencies)
10. [Risks & Mitigations](#10-risks--mitigations)
11. [Explicit Non-Goals](#11-explicit-non-goals)
12. [Glossary](#12-glossary)

---

## 1. Executive Summary

This document defines the business requirements for a **Copy Trading Engine** designed for swing trading. The system automatically replicates all trading activity from a designated master account to one or more client (follower) accounts in near real-time. The engine must guarantee positional parity between master and client accounts at all times, enforce strict risk controls, and provide full auditability.

The system is intended for deployment in a brokerage or portfolio management context where a single master trader's decisions are authoritative, and client accounts must mirror those decisions faithfully, safely, and without human intervention.

---

## 2. Business Objectives


| ID    | Objective                                                                                 | Priority |
| ----- | ----------------------------------------------------------------------------------------- | -------- |
| BO-01 | Replicate all master account trades to client accounts with zero positional drift         | Critical |
| BO-02 | Prevent duplicate order execution across all client accounts                              | Critical |
| BO-03 | Enforce a maximum 2% price deviation (slippage) threshold on all client executions        | Critical |
| BO-04 | Ensure no client account is over-leveraged or exposed beyond available capital            | Critical |
| BO-05 | Provide full traceability and audit trail for every trade event per account               | Critical |
| BO-06 | Guarantee system resilience across restarts, network failures, and broker API disruptions | Critical |
| BO-07 | Provide emergency stop (kill switch) capability for operational safety                    | High     |


---

## 3. Scope

### 3.1 In Scope

- Detection and capture of all master account order lifecycle events
- Persistent storage of all trade events before execution
- Idempotent, parallel order distribution to client accounts
- Slippage protection, partial fill handling, modification/cancellation sync
- Exit synchronization (market exits, partial exits, stop-loss, targets)
- Periodic position reconciliation between master and client accounts
- Client capital protection controls
- Safe retry logic with deduplication
- System-level kill switch
- Immutable logging and audit trail
- Restart recovery with incomplete trade resumption

### 3.2 Out of Scope


| Item                     | Rationale                                            |
| ------------------------ | ---------------------------------------------------- |
| Dashboard / UI           | Separate project; not part of core engine            |
| Multiplier / lot scaling | Not required for initial release                     |
| Capital-based scaling    | Not required for initial release                     |
| Performance analytics    | Separate analytics service to be built independently |


---

## 4. Stakeholders


| Role                 | Responsibility                                   |
| -------------------- | ------------------------------------------------ |
| Master Trader        | Executes trades on the master account            |
| Client / Follower    | Investor whose account mirrors the master        |
| System Administrator | Operates, monitors, and manages the engine       |
| Compliance / Audit   | Reviews trade logs for regulatory compliance     |
| Broker / Exchange    | Provides order execution APIs (WebSocket + REST) |
| Product Owner        | Defines priorities and acceptance criteria       |


---

## 5. Functional Requirements

### FR-01: Master Trade Detection (Authoritative Source)


| Attribute       | Detail                                                                     |
| --------------- | -------------------------------------------------------------------------- |
| **Requirement** | The system must detect all master account order lifecycle events reliably. |
| **Priority**    | Critical                                                                   |


**Capabilities:**

- WebSocket listener for real-time order updates from the broker
- Periodic REST-based reconciliation polling (e.g., every 30 seconds) as a safety net
- Detection of the following event types:
  - New order placed
  - Order modification (price, quantity, type)
  - Order cancellation
  - Partial fill
  - Full fill
  - Order rejection

**Failure Scenarios Addressed:**


| Scenario                        | Mitigation                                |
| ------------------------------- | ----------------------------------------- |
| WebSocket disconnect            | REST reconciliation catches missed events |
| Missed order event              | Periodic polling detects state divergence |
| Server restart during execution | Persistent event store enables recovery   |


**Acceptance Criteria:**
No master order event can be missed even if WebSocket drops.

---

### FR-02: Persistent Trade Event Store (Non-Volatile)


| Attribute       | Detail                                                                            |
| --------------- | --------------------------------------------------------------------------------- |
| **Requirement** | Every master order event must be written to the database before execution starts. |
| **Priority**    | Critical                                                                          |


**Data Model (Minimum Fields):**


| Field             | Description                            |
| ----------------- | -------------------------------------- |
| `master_trade_id` | Unique identifier for the master trade |
| `timestamp`       | Event timestamp                        |
| `instrument`      | Trading instrument / symbol            |
| `side`            | BUY or SELL                            |
| `quantity`        | Order quantity                         |
| `order_type`      | MARKET, LIMIT, SL, SL-M, etc.          |
| `price`           | Order price (or trigger price)         |
| `status`          | Current status of the order            |


**Failure Scenarios Addressed:**


| Scenario                           | Mitigation                              |
| ---------------------------------- | --------------------------------------- |
| Server crash mid-distribution      | Persisted events allow resumption       |
| Duplicate processing after restart | Idempotency checks against stored state |


**Acceptance Criteria:**
System must resume incomplete distributions after restart without duplicating orders.

---

### FR-03: Idempotent Execution Layer (Duplicate Protection)


| Attribute       | Detail                                                        |
| --------------- | ------------------------------------------------------------- |
| **Requirement** | Each client account must execute a master trade exactly once. |
| **Priority**    | Critical                                                      |


**Capabilities:**

- Generate a unique execution ID per client per `master_trade_id`
- Check the execution log before placing any order
- Reject duplicate execution attempts, even on retry

**Failure Scenarios Addressed:**


| Scenario                              | Mitigation                            |
| ------------------------------------- | ------------------------------------- |
| Retry loops                           | Execution ID deduplication            |
| Network timeout after order placement | Status verification before retry      |
| Partial response from broker          | Idempotency key prevents re-execution |


**Acceptance Criteria:**
Zero duplicate positions across clients.

---

### FR-04: Parallel Order Execution Engine


| Attribute       | Detail                                                                   |
| --------------- | ------------------------------------------------------------------------ |
| **Requirement** | Client orders must execute asynchronously but within broker rate limits. |
| **Priority**    | Critical                                                                 |


**Capabilities:**

- Asynchronous worker pool for concurrent order placement
- Rate limit enforcement using a token bucket algorithm
- Backpressure handling when the broker throttles requests

**Failure Scenarios Addressed:**


| Scenario                               | Mitigation                              |
| -------------------------------------- | --------------------------------------- |
| 30+ accounts triggering simultaneously | Worker pool with controlled concurrency |
| API throttling by broker               | Token bucket rate limiter with backoff  |


**Acceptance Criteria:**
No API bans or rate-limit lockouts.

---

### FR-05: 2% Slippage Protection (Spillage Guard)


| Attribute       | Detail                                                                           |
| --------------- | -------------------------------------------------------------------------------- |
| **Requirement** | Client order must not execute if price deviates >2% from master execution price. |
| **Priority**    | Critical                                                                         |


**Logic:**


| Side | Condition                             |
| ---- | ------------------------------------- |
| BUY  | `client_price <= master_price * 1.02` |
| SELL | `client_price >= master_price * 0.98` |


**Behavior on Breach:**

1. Do not execute the order
2. Log the event with status `SLIPPAGE_REJECTED`
3. Raise a system alert for operator review

**Failure Scenarios Addressed:**


| Scenario            | Mitigation                    |
| ------------------- | ----------------------------- |
| Illiquid stocks     | Pre-execution price check     |
| Sudden price gaps   | Hard 2% threshold enforcement |
| Circuit limit moves | Order blocked, alert raised   |


**Acceptance Criteria:**
No client executes beyond 2% deviation from master execution price.

---

### FR-06: Partial Fill Handling


| Attribute       | Detail                                                                            |
| --------------- | --------------------------------------------------------------------------------- |
| **Requirement** | If master order is partially filled, the system must replicate proportional fill. |
| **Priority**    | Critical                                                                          |


**Capabilities:**

- Real-time tracking of master filled quantity
- Replication of only the filled quantity (not the full order quantity)
- Handling of multiple sequential fill events across different price points

**Failure Scenarios Addressed:**


| Scenario                                      | Mitigation                             |
| --------------------------------------------- | -------------------------------------- |
| Master partially fills across multiple prices | Incremental fill replication per event |


**Acceptance Criteria:**
Client net position must equal master net position (not original order quantity).

---

### FR-07: Order Modification & Cancellation Synchronization


| Attribute       | Detail                                                                          |
| --------------- | ------------------------------------------------------------------------------- |
| **Requirement** | Any modification or cancellation on the master must reflect across all clients. |
| **Priority**    | Critical                                                                        |


**Capabilities:**

- Modify price replication
- Cancel order replication
- Stop-loss and target price update propagation

**Failure Scenarios Addressed:**


| Scenario                       | Mitigation                                      |
| ------------------------------ | ----------------------------------------------- |
| Master modifies stop-loss      | Detect modification event, propagate to clients |
| Master cancels a pending order | Cancel all corresponding client orders          |


**Acceptance Criteria:**
No client order remains active when the corresponding master order is cancelled.

---

### FR-08: Exit Synchronization Engine


| Attribute       | Detail                                                     |
| --------------- | ---------------------------------------------------------- |
| **Requirement** | All exit events must replicate exactly to client accounts. |
| **Priority**    | Critical                                                   |


**Exit Types Covered:**

- Market exits (full position close)
- Partial exits (partial quantity reduction)
- Stop-loss trigger
- Target hit

**Failure Scenarios Addressed:**


| Scenario                  | Mitigation                           |
| ------------------------- | ------------------------------------ |
| Master exits 50% quantity | Proportional exit on client accounts |
| Master trails stop-loss   | SL modification synced in real-time  |


**Acceptance Criteria:**
Client net position equals master net position at all times.

---

### FR-09: Position Reconciliation Engine


| Attribute       | Detail                                                                     |
| --------------- | -------------------------------------------------------------------------- |
| **Requirement** | System must verify position alignment periodically (e.g., every 1 minute). |
| **Priority**    | Critical                                                                   |


**Capabilities:**

- Fetch master positions from broker
- Fetch client positions from broker
- Compare instrument-wise net quantity

**On Mismatch:**

1. Log the discrepancy with full detail
2. Attempt auto-correction (configurable, optional)
3. Or disable the client from further trading until manual review

**Failure Scenarios Addressed:**


| Scenario            | Mitigation                                          |
| ------------------- | --------------------------------------------------- |
| Manual client trade | Detected during reconciliation                      |
| Missed event        | Caught by periodic comparison                       |
| API failure         | Flagged during next successful reconciliation cycle |


**Acceptance Criteria:**
No position drift longer than the reconciliation interval.

---

### FR-10: Client Capital Protection Controls


| Attribute       | Detail                                               |
| --------------- | ---------------------------------------------------- |
| **Requirement** | System must prevent catastrophic capital divergence. |
| **Priority**    | Critical                                             |


**Controls:**


| Control                    | Behavior                                     |
| -------------------------- | -------------------------------------------- |
| Max open trades limit      | Reject new trades if limit reached           |
| Insufficient margin check  | Reject trade if margin unavailable           |
| Blocked account check      | Reject trade if account is blocked by broker |
| Repeated failure threshold | Disable account after N consecutive failures |


**Failure Scenarios Addressed:**


| Scenario            | Mitigation                             |
| ------------------- | -------------------------------------- |
| Margin shortfall    | Pre-execution margin validation        |
| Repeated rejections | Auto-disable after threshold           |
| Broker API errors   | Graceful degradation, no blind retries |


**Acceptance Criteria:**
System never over-leverages a client account.

---

### FR-11: Retry Logic (Safe Retry Only)


| Attribute       | Detail                                                                            |
| --------------- | --------------------------------------------------------------------------------- |
| **Requirement** | Retries allowed only when no order confirmation received or order status unknown. |
| **Priority**    | High                                                                              |


**Capabilities:**

- Status check before every retry attempt
- Exponential backoff between retries
- Retry cap (e.g., 3 attempts maximum)

**Failure Scenarios Addressed:**


| Scenario             | Mitigation                            |
| -------------------- | ------------------------------------- |
| Network timeout      | Verify status before retrying         |
| Broker latency spike | Exponential backoff prevents flooding |


**Acceptance Criteria:**
Retry never causes a double position.

---

### FR-12: Kill Switch (System-Level Safety)


| Attribute       | Detail                                          |
| --------------- | ----------------------------------------------- |
| **Requirement** | Emergency stop mechanism for the entire system. |
| **Priority**    | High                                            |


**Modes:**


| Mode             | Behavior                                         |
| ---------------- | ------------------------------------------------ |
| Stop New Entries | Block all new order placements; allow exits only |
| Full Halt        | Stop all trading activity immediately            |


**Failure Scenarios Addressed:**


| Scenario                    | Mitigation                               |
| --------------------------- | ---------------------------------------- |
| Master account compromised  | Immediate full halt                      |
| Erroneous strategy behavior | Stop new entries, allow protective exits |
| Market crash event          | Full halt to prevent further exposure    |


---

### FR-13: Logging & Audit Trail (Immutable)


| Attribute       | Detail                                                                       |
| --------------- | ---------------------------------------------------------------------------- |
| **Requirement** | Every trade-related action must be logged immutably for audit and debugging. |
| **Priority**    | Critical                                                                     |


**Events Logged:**


| Log Entry                | Description                               |
| ------------------------ | ----------------------------------------- |
| Master trade event       | Original event from master account        |
| Client execution request | Order request sent for each client        |
| Broker response          | Response received from broker API         |
| Slippage calculation     | Price deviation computation and outcome   |
| Retry attempts           | Each retry attempt with reason and result |
| Final state              | Terminal state of each execution          |


**Failure Scenarios Addressed:**


| Scenario         | Mitigation                                   |
| ---------------- | -------------------------------------------- |
| Investor dispute | Full trade trail available per account       |
| Regulatory audit | Immutable, timestamped logs                  |
| Debugging desync | Complete event chain for root cause analysis |


**Acceptance Criteria:**
Full traceability per trade per account.

---

### FR-14: Restart Recovery Mechanism


| Attribute       | Detail                                                                           |
| --------------- | -------------------------------------------------------------------------------- |
| **Requirement** | On system restart, the engine must recover and resume all incomplete operations. |
| **Priority**    | Critical                                                                         |


**Recovery Actions:**

1. Reload all incomplete / in-progress trades from the persistent store
2. Resume pending client distributions
3. Resume the reconciliation engine

**Failure Scenarios Addressed:**


| Scenario                         | Mitigation                              |
| -------------------------------- | --------------------------------------- |
| Server crash during market hours | Stateful recovery from persistent store |


---

## 6. Non-Functional Requirements


| ID     | Requirement    | Specification                                                         |
| ------ | -------------- | --------------------------------------------------------------------- |
| NFR-01 | Latency        | Master-to-client order propagation < 500ms under normal conditions    |
| NFR-02 | Throughput     | Support 30+ concurrent client accounts per master                     |
| NFR-03 | Availability   | 99.9% uptime during market hours                                      |
| NFR-04 | Durability     | Zero data loss on crash — all events persisted before processing      |
| NFR-05 | Recoverability | Full operational recovery within 60 seconds of restart                |
| NFR-06 | Scalability    | Horizontally scalable worker pool for client execution                |
| NFR-07 | Security       | API keys encrypted at rest; TLS for all broker communication          |
| NFR-08 | Auditability   | Immutable append-only logs with retention per regulatory requirements |


---

## 7. Business Rules & Constraints


| ID    | Rule                                                                                    |
| ----- | --------------------------------------------------------------------------------------- |
| BR-01 | A client order must never be placed without a corresponding persisted master event      |
| BR-02 | Slippage threshold is fixed at 2% — configurable in future phases                       |
| BR-03 | Reconciliation interval must not exceed 1 minute during market hours                    |
| BR-04 | Retry cap is 3 attempts with exponential backoff                                        |
| BR-05 | Client accounts auto-disable after repeated execution failures (threshold configurable) |
| BR-06 | Kill switch must be operable without code deployment (runtime control)                  |
| BR-07 | All timestamps must be in UTC with millisecond precision                                |


---

## 8. Acceptance Criteria Summary


| FR    | Criterion                                                                |
| ----- | ------------------------------------------------------------------------ |
| FR-01 | No master order event can be missed even if WebSocket drops              |
| FR-02 | Resume incomplete distributions after restart without duplicating orders |
| FR-03 | Zero duplicate positions across clients                                  |
| FR-04 | No API bans or rate-limit lockouts                                       |
| FR-05 | No client executes beyond 2% deviation from master execution price       |
| FR-06 | Client net position equals master net position (not original order qty)  |
| FR-07 | No client order remains active when master order is cancelled            |
| FR-08 | Client net position equals master net position at all times              |
| FR-09 | No position drift longer than reconciliation interval                    |
| FR-10 | System never over-leverages a client account                             |
| FR-11 | Retry never causes a double position                                     |
| FR-13 | Full traceability per trade per account                                  |


---

## 9. Assumptions & Dependencies

### Assumptions


| ID   | Assumption                                                                        |
| ---- | --------------------------------------------------------------------------------- |
| A-01 | Broker provides WebSocket and REST APIs for order management and position queries |
| A-02 | Broker API supports idempotency keys or order tagging for deduplication           |
| A-03 | All client accounts are pre-registered and authorized for copy trading            |
| A-04 | Master account trades only in instruments tradeable by all client accounts        |
| A-05 | Network connectivity to broker is generally stable with transient failures only   |


### Dependencies


| ID   | Dependency                                               |
| ---- | -------------------------------------------------------- |
| D-01 | Broker API availability and rate limit documentation     |
| D-02 | Database infrastructure for persistent trade store       |
| D-03 | Message queue / event bus infrastructure (if applicable) |
| D-04 | Monitoring and alerting infrastructure for system alerts |


---

## 10. Risks & Mitigations


| ID   | Risk                                                | Impact   | Probability | Mitigation                                       |
| ---- | --------------------------------------------------- | -------- | ----------- | ------------------------------------------------ |
| R-01 | Broker API downtime during market hours             | Critical | Medium      | REST fallback, retry logic, kill switch          |
| R-02 | WebSocket disconnection causing missed events       | High     | High        | REST reconciliation every 30 seconds             |
| R-03 | Server crash during active distribution             | Critical | Low         | Persistent store + restart recovery              |
| R-04 | Slippage in illiquid instruments                    | High     | Medium      | 2% hard threshold, alert, and rejection          |
| R-05 | Client performs manual trade causing position drift | Medium   | Medium      | Reconciliation engine detects and flags/corrects |
| R-06 | Broker rate limiting causing execution delays       | High     | High        | Token bucket, backpressure, staggered execution  |
| R-07 | Duplicate execution due to network ambiguity        | Critical | Medium      | Idempotency layer, execution log check           |


---

## 11. Explicit Non-Goals

The following items are **intentionally excluded** from this release to maintain scope clarity:

- **Dashboard / UI** — No front-end or monitoring dashboard
- **Multiplier Logic** — No lot-size multiplication per client
- **Capital Scaling Logic** — No proportional allocation based on client capital
- **Performance Analytics** — No P&L tracking, Sharpe ratio, or strategy metrics

These may be addressed in future phases.

---

## 12. Glossary


| Term           | Definition                                                                       |
| -------------- | -------------------------------------------------------------------------------- |
| Master Account | The authoritative trading account whose trades are replicated                    |
| Client Account | A follower account that mirrors the master's trades                              |
| Copy Trading   | Automatic replication of trades from a master to client accounts                 |
| Slippage       | Difference between expected execution price and actual execution price           |
| Idempotency    | Property ensuring an operation produces the same result regardless of repetition |
| Token Bucket   | Rate limiting algorithm that allows bursts up to a limit then enforces a rate    |
| Reconciliation | Process of comparing and aligning positions between master and client accounts   |
| Kill Switch    | Emergency mechanism to halt all or part of trading activity                      |
| Backpressure   | Flow control mechanism to slow producers when consumers are overwhelmed          |
| Partial Fill   | When only a portion of an order's quantity is executed                           |


---

## System Guarantee Objective

> The system must guarantee:
>
> 1. Client positions always equal master positions
> 2. No duplicate orders
> 3. No execution beyond 2% price deviation
> 4. No missed exits
> 5. No capital overexposure

---

*End of Document*
