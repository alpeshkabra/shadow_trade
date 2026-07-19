"""Distribution Orchestrator + Parallel Execution Engine (HLD 4.3 & 4.4).

For each master trade event this fans the order out to every eligible client
account, running each client's pipeline concurrently in a thread pool. The
per-client pipeline is:

    kill switch → idempotency → capital/margin → slippage(2%) → execute(+retry)

Every branch is audited, and every attempt is written to ``client_execution_log``.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List

from .audit import AuditLogger
from .brokers.base import BrokerClient, OrderRequest
from .capital import CapitalValidator
from .config import Settings
from .db import Database
from .enums import EventType, ExecutionStatus, Outcome
from .kill_switch import KillSwitchController
from .models import ClientAccount, ClientExecutionLog
from .slippage import SlippageGuard


@dataclass
class MasterEvent:
    """A normalized master trade signal handed to the orchestrator."""

    master_trade_id: str
    event_seq: int
    event_type: EventType
    instrument: str
    side: "OrderSide"  # noqa: F821 - imported lazily below to avoid cycle
    quantity: Decimal
    order_type: "OrderType"  # noqa: F821
    price: Decimal


@dataclass
class ClientOutcome:
    client_account_id: str
    status: ExecutionStatus
    detail: str = ""
    slippage_pct: Decimal = Decimal("0")
    broker_order_id: str = ""


class DistributionOrchestrator:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        kill_switch: KillSwitchController,
        audit: AuditLogger,
        client_brokers: Dict[str, BrokerClient],
    ):
        self._db = db
        self._settings = settings
        self._kill = kill_switch
        self._audit = audit
        self._brokers = client_brokers
        self._slippage = SlippageGuard(settings.max_slippage_pct)
        self._capital = CapitalValidator()

    # -- public API ---------------------------------------------------------
    def distribute(self, event: MasterEvent) -> List[ClientOutcome]:
        if self._kill.is_active():
            self._audit.record(
                "DistributionOrchestrator",
                "EVENT_DROPPED_KILL_SWITCH",
                Outcome.SKIPPED,
                {"master_trade_id": event.master_trade_id},
                master_trade_id=event.master_trade_id,
            )
            return []

        with self._db.session_scope() as s:
            accounts = s.query(ClientAccount).filter(ClientAccount.enabled.is_(True)).all()
            account_ids = [a.client_account_id for a in accounts]

        workers = min(self._settings.max_execution_workers, max(1, len(account_ids)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(
                pool.map(lambda cid: self._process_client(cid, event), account_ids)
            )
        return outcomes

    # -- per-client pipeline ------------------------------------------------
    def _process_client(self, client_account_id: str, event: MasterEvent) -> ClientOutcome:
        execution_id = f"{client_account_id}:{event.master_trade_id}:{event.event_seq}"

        # 1) Idempotency — never place the same client order twice (HLD 4.3).
        if self._already_executed(execution_id):
            self._audit.record(
                "IdempotencyChecker",
                "DUPLICATE_SKIPPED",
                Outcome.SKIPPED,
                {"execution_id": execution_id},
                master_trade_id=event.master_trade_id,
                client_account_id=client_account_id,
            )
            return ClientOutcome(client_account_id, ExecutionStatus.SKIPPED_DUPLICATE)

        with self._db.session_scope() as s:
            account = s.get(ClientAccount, client_account_id)
            copy_ratio = Decimal(account.copy_ratio)
            open_trades = (
                s.query(ClientExecutionLog)
                .filter(
                    ClientExecutionLog.client_account_id == client_account_id,
                    ClientExecutionLog.status == ExecutionStatus.FILLED.value,
                )
                .count()
            )
            # Detach a plain snapshot for use outside the session.
            account_snapshot = ClientAccount(
                client_account_id=account.client_account_id,
                name=account.name,
                enabled=account.enabled,
                copy_ratio=account.copy_ratio,
                available_margin=account.available_margin,
                max_open_trades=account.max_open_trades,
            )

        qty = (Decimal(event.quantity) * copy_ratio)
        order = OrderRequest(
            instrument=event.instrument,
            side=event.side,
            quantity=qty,
            order_type=event.order_type,
            price=event.price,
        )
        broker = self._brokers[client_account_id]

        # 2) Capital / margin check (HLD 4.3).
        decision = self._capital.validate(
            account_snapshot, order, event.price, open_trades
        )
        if not decision.approved:
            return self._reject(
                execution_id, client_account_id, event, order,
                ExecutionStatus.REJECTED, decision.reason,
            )

        # 3) Slippage guard — compare the client's current price to the master
        #    fill price BEFORE sending the order (HLD 4.3).
        client_price = broker.last_price(event.instrument)
        allowed, pct = self._slippage.evaluate(event.price, client_price)
        if not allowed:
            return self._reject(
                execution_id, client_account_id, event, order,
                ExecutionStatus.SLIPPAGE_REJECTED,
                f"slippage {pct:.4f}% > {self._settings.max_slippage_pct}%",
                slippage=pct,
            )

        # 4) Execute with bounded retries on transient failures (HLD 8.2).
        return self._execute_with_retry(
            execution_id, client_account_id, event, order, broker, pct
        )

    # -- helpers ------------------------------------------------------------
    def _already_executed(self, execution_id: str) -> bool:
        with self._db.session_scope() as s:
            return (
                s.query(ClientExecutionLog)
                .filter(ClientExecutionLog.execution_id == execution_id)
                .first()
                is not None
            )

    def _execute_with_retry(
        self, execution_id, client_account_id, event, order, broker, pct
    ) -> ClientOutcome:
        attempts = 0
        last_error = ""
        while attempts <= self._settings.max_order_retries:
            try:
                result = broker.place_order(order)
                self._write_execution(
                    execution_id, client_account_id, event, order,
                    ExecutionStatus.FILLED, retry_count=attempts,
                    slippage=pct, broker_order_id=result.broker_order_id,
                )
                self._audit.record(
                    "ParallelExecutionEngine", "ORDER_FILLED", Outcome.SUCCESS,
                    {
                        "execution_id": execution_id,
                        "broker_order_id": result.broker_order_id,
                        "fill_price": str(result.fill_price),
                        "slippage_pct": str(pct),
                    },
                    master_trade_id=event.master_trade_id,
                    client_account_id=client_account_id,
                )
                return ClientOutcome(
                    client_account_id, ExecutionStatus.FILLED,
                    slippage_pct=pct, broker_order_id=result.broker_order_id,
                )
            except Exception as exc:  # noqa: BLE001 - broker adapters raise many types
                attempts += 1
                last_error = str(exc)

        return self._reject(
            execution_id, client_account_id, event, order,
            ExecutionStatus.FAILED, f"exhausted retries: {last_error}",
            slippage=pct, retry_count=attempts,
        )

    def _reject(
        self, execution_id, client_account_id, event, order, status, reason,
        slippage=Decimal("0"), retry_count=0,
    ) -> ClientOutcome:
        self._write_execution(
            execution_id, client_account_id, event, order, status,
            retry_count=retry_count, slippage=slippage, error_detail=reason,
        )
        self._audit.record(
            "DistributionOrchestrator", status.value, Outcome.REJECTED,
            {"execution_id": execution_id, "reason": reason},
            master_trade_id=event.master_trade_id,
            client_account_id=client_account_id,
        )
        return ClientOutcome(client_account_id, status, detail=reason, slippage_pct=slippage)

    def _write_execution(
        self, execution_id, client_account_id, event, order, status,
        retry_count=0, slippage=Decimal("0"), broker_order_id=None, error_detail=None,
    ) -> None:
        with self._db.session_scope() as s:
            s.add(
                ClientExecutionLog(
                    execution_id=execution_id,
                    master_trade_id=event.master_trade_id,
                    client_account_id=client_account_id,
                    instrument=event.instrument,
                    side=event.side.value,
                    quantity=order.quantity,
                    order_type=event.order_type.value,
                    price=event.price,
                    broker_order_id=broker_order_id,
                    status=status.value,
                    slippage_pct=slippage,
                    retry_count=retry_count,
                    error_detail=error_detail,
                )
            )
