# ShadowTrade v1 — Technical FAQ

**Date:** 19 March 2026
**Reference:** LLD v1.4 (LLD-CTE-2026-001)

---

## Table of Contents

1. [Event Deduplication Across Three Channels](#1-how-do-you-unify-websocket-rest-and-webhook-events-without-duplication)
2. [Single Master Trade Execution Flow](#2-what-is-the-exact-execution-flow-of-a-single-master-trade)
3. [Partial Fill Replication Strategy](#3-will-partial-fills-be-replicated-as-fill-events-or-full-order-intent)
4. [Partial Exit Handling](#4-how-are-partial-exits-handled-across-clients)
5. [Retry Logic and Duplicate Prevention](#5-how-does-retry-logic-prevent-duplicate-orders)
6. [Reconciliation Mismatch Actions](#6-what-happens-when-reconciliation-detects-a-mismatch)
7. [Slippage Validation Reference Price](#7-what-reference-price-is-used-for-2-slippage-validation)
8. [Restart Recovery Mechanism](#8-how-does-the-system-resume-incomplete-executions-after-restart)
9. [Capital Protection Rules](#9-what-are-the-exact-capital-protection-rules)
10. [Token Expiry and Login Failure Handling](#10-how-are-token-expiry--login-failures-handled-per-client)
11. [Daily Token Management and Client Authentication](#11-how-is-daily-token-management-handled-do-clients-need-to-manually-authenticate)

---

## 1. How do you unify WebSocket, REST, and Webhook events without duplication?

**Three channels, one deduplication gate.** All three channels funnel into the same method: `EventStore.persist_master_event()`. This method uses a PostgreSQL `INSERT ... ON CONFLICT DO NOTHING` on a unique constraint defined as:

```sql
CONSTRAINT uq_master_event UNIQUE (master_trade_id, event_type, filled_quantity)
```

The composite key `(master_trade_id, event_type, filled_quantity)` means the same order event can arrive via postback webhook, WebSocket, AND REST poll — only the **first arrival** inserts a row. The method returns `True` if a row was inserted (new event) and `False` if it hit the conflict (duplicate). Only new events are forwarded to the orchestrator.

**Per-channel flow:**

- **Postback webhook** (Channel 1, primary): Arrives at `POST /api/v1/postback/zerodha` → SHA-256 checksum verified using `order_id + order_timestamp + api_secret` → `persist_master_event()` → if new, distribute to clients.

- **WebSocket / KiteTicker** (Channel 2): `on_order_update` callback fires on every order lifecycle change → same `persist_master_event()` → if new, distribute. This channel catches **manual trades** placed on Kite web/mobile that the postback webhook would miss (postback only fires for API-placed orders).

- **REST polling** (Channel 3, safety net): Every 30 seconds, calls `get_all_orders()` and compares against an in-memory `_known_order_states` dictionary (`{order_id: "status:filled_qty"}`). Only changed orders pass through `persist_master_event()`. This catches anything missed by the other two channels due to network issues.

**Guarantee:** The in-memory dict is an optimization to reduce unnecessary DB writes, but the PostgreSQL unique constraint is the **hard guarantee** against duplication. Even if all three channels fire simultaneously for the same event, exactly one INSERT succeeds.

---

## 2. What is the exact execution flow of a single master trade?

Step-by-step for a master BUY 1000 SBIN that fills completely:

```
STEP 1 — DETECTION
  Postback webhook / WebSocket / REST poll
  └─ MasterTradeDetector receives order update
  └─ _classify_event(order) → "FULL_FILL"

STEP 2 — PERSISTENCE (write-ahead, before any processing)
  └─ EventStore.persist_master_event()
     └─ INSERT into master_trade_events table
     └─ Returns True (new event) → proceed to distribution

STEP 3 — KILL SWITCH CHECK
  └─ KillSwitch.get_mode()
     ├─ DISABLED → proceed
     ├─ STOP_NEW_ENTRIES → block new buys, allow exits (sells/cancels)
     └─ FULL_HALT → block everything, log to audit, return

STEP 4 — QUANTITY CALCULATION (partial fill handling)
  └─ _calculate_distribution_quantity()
     └─ Check distributed_fill_tracker table
     └─ 1000 filled - 0 already distributed = 1000 to distribute

STEP 5 — CLIENT DISTRIBUTION (parallel for all 30 clients)
  └─ asyncio.gather(*[_process_client(client) for client in active_clients])

STEP 6 — PER-CLIENT VALIDATION (runs in parallel for each client)
  └─ Generate execution_id: "client_001:ORDER123:FULL_FILL:1000"
  ├─ FR-03 Idempotency: check_execution_exists(execution_id) → skip if duplicate
  ├─ FR-10 Capital Guard: account active? failures < 5? open trades < 20?
  ├─ FR-05 Slippage Guard: |master_avg_price - current_price| < 2%?
  ├─ Record execution as PENDING in client_execution_log
  └─ Submit to ExecutionEngine

STEP 7 — ORDER PLACEMENT (per-client, independently rate-limited)
  └─ ExecutionEngine._execute()
     ├─ Acquire per-API-key rate limiter token (blocks if exhausted)
     ├─ broker.place_order(OrderRequest) with 5-second timeout
     ├─ On success → update status to SUBMITTED, log latency
     ├─ On timeout → RetryManager.handle_timeout()
     └─ On error → RetryManager.handle_error()

STEP 8 — ONGOING RECONCILIATION (every 60 seconds)
  └─ Compare master positions vs all client positions
  └─ Flag any mismatches
```

**Total time (30 clients, 30 API keys):** < 0.5 seconds from detection to all 30 orders placed.

---

## 3. Will partial fills be replicated as fill events or full order intent?

**As incremental fill events — clients execute multiple times.**

### Scenario: Master buys 1000, gets filled 300 → 200 → 500

The `distributed_fill_tracker` table keeps a running total per `master_trade_id`:

```
Fill Event 1: Broker reports filled_quantity = 300
  already_distributed = 0
  incremental = 300 - 0 = 300
  → Distribute BUY 300 to all clients
  → Update tracker: total_distributed = 300

Fill Event 2: Broker reports filled_quantity = 500 (cumulative)
  already_distributed = 300
  incremental = 500 - 300 = 200
  → Distribute BUY 200 to all clients
  → Update tracker: total_distributed = 500

Fill Event 3: Broker reports filled_quantity = 1000 (complete)
  already_distributed = 500
  incremental = 1000 - 500 = 500
  → Distribute BUY 500 to all clients
  → Update tracker: total_distributed = 1000
```

**Answer: Clients execute 3 times (300 + 200 + 500).** They do NOT aggregate and wait. This is by design:

- Each partial fill may have a different price → slippage check runs per fill
- Clients get position exposure as fast as possible (no waiting for full fill)
- Each execution has a unique idempotency key that includes `filled_quantity`:
  - `"client_001:ORDER123:PARTIAL_FILL:300"`
  - `"client_001:ORDER123:PARTIAL_FILL:500"`
  - `"client_001:ORDER123:FULL_FILL:1000"`
- Duplicates from the 3 detection channels are impossible because of the DB unique constraint

---

## 4. How are partial exits handled across clients?

### Scenario: Master holds 1000 shares, sells 400

The master's sell order triggers a `FULL_FILL` event with `side=SELL`, `quantity=400`. The orchestrator distributes `SELL 400` to **all** clients.

**Current model: Absolute quantity replication (1:1 mirror)**

- Master holds 1000, client holds 1000
- Master sells 400 → client sells 400
- Master now holds 600, client now holds 600

This is validated every 60 seconds by the reconciliation engine, which compares `net_quantity` per instrument between master and each client.

**If the sell also partially fills (e.g., 400 fills as 150 → 250):**

The same incremental distribution logic from Question 3 applies:
- Fill 1: Distribute SELL 150 to all clients
- Fill 2: Distribute SELL 250 to all clients

**Important note on proportional exits:**

The current design mirrors exact quantities (1:1). If clients hold different quantities than the master (e.g., due to different capital levels), a proportional scaling module would be needed. This is not currently in scope but can be added as an enhancement in the orchestrator's `_calculate_distribution_quantity()` method if the requirement is percentage-based exits rather than absolute mirroring.

---

## 5. How does retry logic prevent duplicate orders?

The RetryManager uses a **check-before-retry** protocol:

```
1. TIMEOUT or ERROR occurs during place_order()
   (We don't know if the order reached the broker or not)

2. BEFORE retrying, the system checks order state:
   a. Call broker.get_all_orders()
   b. Search for any order whose raw payload contains the execution tag
      (first 20 chars of execution_id, set in OrderRequest.tag field)
   c. If found AND status is COMPLETE / OPEN / TRIGGER_PENDING:
      → The order WAS placed successfully; we just lost the response
      → Update execution to SUBMITTED with the found broker_order_id
      → DO NOT retry — return immediately

3. If order NOT found (truly never reached the broker):
   → Safe to retry
   → Place order with same tag
   → Exponential backoff: 1s → 2s → 4s (max 3 attempts)

4. After 3 failed retries:
   → Mark execution as FAILED
   → Log to immutable audit trail
   → Increment client's consecutive_failures counter
```

**Three layers of duplicate prevention:**

| Layer | Mechanism | Scope |
|-------|-----------|-------|
| Orchestrator | `execution_id` uniqueness check in DB | Prevents same event from being processed twice |
| Retry Manager | Tag-based order lookup before each retry | Prevents placing an order that already exists at broker |
| Database | `UNIQUE(execution_id)` on `client_execution_log` | Hard constraint — even concurrent races can't create duplicates |

---

## 6. What happens when reconciliation detects a mismatch?

The reconciliation engine runs every 60 seconds during market hours. On mismatch detection:

### Default behavior (auto_correct_on_mismatch = False, recommended):

1. **Log** at ERROR level: client_id, instrument, master_qty, client_qty, diff
2. **Increment** `RECONCILIATION_MISMATCHES` Prometheus metric (for alerting)
3. **Record** in `reconciliation_snapshots` table with `match_status = 'MISMATCHED'`
4. **Write** to immutable `audit_log` with full context
5. **Expose** via `GET /api/v1/reconciliation/status` API endpoint
6. **No automatic action** — requires human review and manual intervention

### If enabled (auto_correct_on_mismatch = True):

All of the above, PLUS:

1. Calculate diff: `master_qty - client_qty`
2. Place a corrective MARKET order:
   - If diff > 0 (client is short) → BUY `abs(diff)` shares
   - If diff < 0 (client is long) → SELL `abs(diff)` shares
3. Log the auto-correction to audit trail

**Recommendation:** Auto-correction is OFF by default. Automatic corrective market orders in live markets carry risk (wrong price, flash crash, etc.). The recommended workflow is:

```
Detect mismatch → Alert operator → Human reviews context → Manual correction if needed
```

Auto-correction can be enabled for trusted, stable-market conditions via the `AUTO_CORRECT_ON_MISMATCH=true` environment variable.

---

## 7. What reference price is used for 2% slippage validation?

**The master account's average fill price (`average_price`) from the fill event.**

### Validation rules:

```
BUY order:  client_price must be <= master_price × 1.02
SELL order: client_price must be >= master_price × 0.98
```

### When the check happens:

| Timing | Reference Price | Client Price | Behavior |
|--------|----------------|--------------|----------|
| Pre-execution (market orders) | Master's `average_price` | Unknown (market order) | **Passes through** — can't know client fill price yet |
| Pre-execution (limit orders) | Master's `average_price` | Limit price from order | Compared against master |
| Post-execution | Master's `average_price` | Client's actual `average_price` | Verified in reconciliation |

### Example:

- Master partial fill: 300 shares at average price ₹470
- Slippage threshold: 2%
- For BUY: max allowed client price = ₹470 × 1.02 = **₹479.40**
- For SELL: min allowed client price = ₹470 × 0.98 = **₹460.60**

If the slippage check fails:
- Execution is recorded as `SLIPPAGE_REJECTED` in `client_execution_log`
- The actual slippage percentage is stored in `slippage_pct` column
- Audit log entry is created with the exact deviation
- The client does NOT execute this trade

**The threshold (2%) is configurable** via `SLIPPAGE_THRESHOLD_PCT` environment variable.

---

## 8. How does the system resume incomplete executions after restart?

The `RecoveryManager` runs at application startup, **before** the trade detector begins listening for new events:

```
STEP 1 — FIND INCOMPLETE TRADES
  Query: SELECT all master_trade_events
         WHERE status NOT IN ('COMPLETE', 'CANCELLED', 'REJECTED', 'FULL_FILL')
  → Returns trades that were in-progress when the system went down

STEP 2 — FOR EACH INCOMPLETE TRADE:
  a. Reconstruct an OrderInfo object from the DB row
  b. Classify as "PARTIAL_FILL" (if filled_quantity > 0) or "NEW"
  c. Feed it back into DistributionOrchestrator.distribute()

STEP 3 — ORCHESTRATOR RE-DISTRIBUTION:
  a. Check distributed_fill_tracker → what was already sent to clients
  b. For each client, generate execution_id (deterministic, same formula)
  c. Idempotency check: check_execution_exists(execution_id)
     ├─ If exists → this client already got the trade before crash → SKIP
     └─ If not exists → this client missed it → EXECUTE
  d. Only clients who MISSED the distribution get new executions

STEP 4 — LOG AND PROCEED
  Log recovery count → start normal trade detection
```

**Why this works without duplicates:**

- The write-ahead pattern means every event is in the DB before processing starts
- Every execution has a deterministic `execution_id` based on `client_id:master_trade_id:event_type:filled_quantity`
- The same ID generated before the crash will be generated again during recovery
- The DB tells us which clients already processed it and which didn't

**Recovery time:** < 5 seconds (FastAPI cold start) + time to re-process incomplete trades. The system is fully operational within ~10 seconds of restart.

---

## 9. What are the exact capital protection rules?

Three rules checked in sequence by `CapitalGuard.validate()` before every execution:

### Rule 1: Account Status

```
IF client.status != 'ACTIVE' THEN REJECT
```

Possible statuses: `ACTIVE`, `DISABLED`, `BLOCKED`

- `DISABLED` — manually disabled by admin (e.g., client requested pause)
- `BLOCKED` — automatically blocked by system (e.g., consecutive failures exceeded)
- Only `ACTIVE` accounts receive trade distributions

### Rule 2: Consecutive Failure Limit

```
IF client.consecutive_failures >= client.max_consecutive_failures THEN REJECT
```

- Default threshold: **5 consecutive failures**
- Counter increments on: order REJECTED, FAILED, or max retries exhausted
- Counter resets to 0 on: successful order placement
- Configurable per client in `client_accounts.max_consecutive_failures`
- Purpose: prevents throwing money at a broken adapter/expired token

### Rule 3: Maximum Open Trades

```
IF COUNT(client_execution_log WHERE status IN ('PENDING','SUBMITTED')) >= client.max_open_trades THEN REJECT
```

- Default threshold: **20 open (non-terminal) trades per client**
- Counts executions in `PENDING` or `SUBMITTED` status (not yet filled/cancelled/rejected)
- Configurable per client in `client_accounts.max_open_trades`
- Purpose: prevents overexposure if fills are slow or orders are stuck

### On rejection:

| Action | Detail |
|--------|--------|
| Execution status | `CAPITAL_REJECTED` in `client_execution_log` |
| Audit log | Entry with specific rejection reason |
| Prometheus metric | `CLIENT_EXECUTIONS_FAILED` counter incremented |
| Other clients | NOT affected — rejection is per-client |
| Other orders | Client's existing open orders are NOT cancelled |

---

## 10. How are token expiry / login failures handled per client?

### Token Lifecycle

Zerodha access tokens expire daily at ~6:00 AM IST. Each client has their own API key and must authenticate once per day via OAuth2.

```
1. Admin/client visits: GET /auth/login/{account_id}
   → Redirected to Zerodha OAuth login page

2. Client logs in with their Zerodha credentials

3. Zerodha redirects back: GET /auth/callback?request_token=XXX&account_id=YYY

4. System exchanges request_token for access_token using client's api_secret

5. access_token stored in client_accounts table:
   - access_token = <encrypted token>
   - token_expires_at = next day 6:00 AM IST
   - last_login_at = current timestamp
```

### Pre-Market Validation (Automated, 8:45 AM IST)

`TokenManager.validate_all_tokens()` runs as a scheduled job, 15 minutes before market open:

1. For each active client:
   - Check `token_expires_at` — if in the past → mark invalid
   - Call `kite.profile()` — lightweight API call to verify token actually works
2. Results: `{account_id: True/False}` for all clients
3. If ANY tokens are expired/invalid:
   - Log at **CRITICAL** level with list of affected account IDs
   - Alert sent (email/Slack — configurable)
   - Those clients are **excluded** from broker adapter pool

### At Application Startup

`TokenManager.build_broker_adapters()` only creates adapters for clients with valid, non-expired tokens. Clients with missing/expired tokens are silently skipped with a warning log. They will NOT receive trade distributions until they re-authenticate.

### Mid-Session Token Expiry (Edge Case)

If a token expires or becomes invalid during trading hours:

1. The broker adapter's API call fails (Zerodha returns 403)
2. Execution engine catches the exception → sends to RetryManager
3. RetryManager attempts retries (1s, 2s, 4s) — all fail with 403
4. After 3 retries → execution marked as `FAILED`
5. `consecutive_failures` counter increments for that client
6. If counter hits threshold (5) → CapitalGuard auto-blocks further trades
7. Client must re-authenticate via `/auth/login/{account_id}` to resume

### Monitoring Endpoint

`GET /api/v1/auth/status` returns real-time token validity:

```json
{
  "total": 30,
  "valid": 28,
  "expired": ["client_029", "client_030"]
}
```

---

## 11. How is daily token management handled? Do clients need to manually authenticate?

### The Constraint (Zerodha / SEBI Requirement)

Zerodha's Kite Connect API **requires a manual browser-based login every day** for each API key. This is not a design choice — it is a hard constraint imposed by Zerodha in compliance with SEBI regulations:

- Access tokens expire daily at ~6:00 AM IST
- To get a new token, the actual account holder must:
  1. Open a browser
  2. Visit Zerodha's login page
  3. Enter their Zerodha user ID + password
  4. Complete 2FA (TOTP/PIN)
  5. Zerodha redirects back with a `request_token`
- There is **no API to refresh tokens programmatically** — Zerodha deliberately requires the human account holder to authorize access each day
- Automating the login via headless browser (Selenium, Puppeteer, etc.) violates Zerodha's Terms of Service and can result in permanent API key bans

This constraint applies to **every** platform built on Kite Connect, not just ours.

### Our Approach: Link-Based Daily Login (Minimal Friction)

We minimize the daily effort to a **single click + 30-second Zerodha login** per client:

```
DAILY FLOW (per client, ~30 seconds):

1. Admin sends personalized link to each client (WhatsApp/email/SMS)
   Link: https://yourdomain.com/api/v1/auth/login/client_001

2. Client clicks the link → redirected to Zerodha's official login page

3. Client enters their Zerodha credentials + 2FA (on Zerodha's page, not ours)

4. Zerodha redirects back to our server automatically

5. System exchanges the temporary request_token for an access_token
   and stores it securely in the database

6. Client sees "Token refreshed successfully" — done for the day
```

**The client does NOT need to:**
- Know anything about APIs or tokens
- Visit any admin panel
- Do anything beyond clicking a link and logging into Zerodha

### Pre-Market Monitoring (Automated)

Every day at 8:45 AM IST (15 minutes before market opens), the system automatically:

1. Validates every client's token by making a lightweight API call (`kite.profile()`)
2. Identifies clients who haven't logged in yet
3. Sends alerts (configurable: email/Slack/webhook) with the list of expired tokens
4. The `/auth/status` API endpoint shows real-time status:

```json
{
  "total": 30,
  "valid": 28,
  "expired": ["client_029", "client_030"]
}
```

Clients with expired tokens are automatically **excluded** from trade distribution — they will not receive any orders until they authenticate. Their existing positions are unaffected.

### What Happens If a Client Doesn't Log In?

| Scenario | System Behavior |
|----------|----------------|
| Client doesn't log in before market opens | Excluded from all trade distributions for the day. Alert sent to admin. |
| Client logs in late (e.g., 10:30 AM) | Token is activated immediately. Client starts receiving trades from that point onward. Missed trades are NOT retroactively executed. |
| Token expires mid-session (rare) | Broker API calls fail → retry manager attempts 3 retries → execution marked FAILED → consecutive_failures incremented → admin alerted |

### Future Enhancement: Zerodha Publisher Mode

We are exploring Zerodha's **Publisher/Platform mode**, which would allow:
- Clients to authorize the platform **once** (not daily)
- The platform retains API access without daily re-authentication
- Requires formal business registration with Zerodha (apply at connect@zerodha.com)

If approved, this would eliminate the daily login requirement entirely. This is a parallel track that does not affect the current implementation.

### Future Enhancement: Admin Dashboard

A web-based admin dashboard showing:
- Real-time token status for all clients (green/red indicators)
- One-click "send reminder" to clients with expired tokens
- Login history and token refresh timestamps
- Bulk link generation for WhatsApp/email distribution

This is planned as a post-MVP enhancement and does not block the core trading engine.

### Summary

| Aspect | Detail |
|--------|--------|
| Client daily effort | Click a link + Zerodha login (~30 seconds) |
| Admin daily effort | Send links to clients + monitor status |
| Automated monitoring | 8:45 AM IST token check + alerts |
| Expired token behavior | Client excluded from trades, admin alerted |
| Late login support | Yes — client starts receiving trades immediately after login |
| Programmatic refresh | Not possible (Zerodha/SEBI restriction) |
| Future improvement | Zerodha Publisher mode (one-time auth) |

---

*All answers reference LLD v1.4 (LLD-CTE-2026-001). Code implementations are in the corresponding module files as specified in LLD Section 7.*
