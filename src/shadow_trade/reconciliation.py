"""Reconciliation Engine (HLD section 4.5).

Periodically compares each client's net positions against the master's,
instrument by instrument. On a mismatch it either auto-corrects (place the
delta order) or disables the client, per configuration — backing the
"client positions always equal master positions" guarantee.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List

from .audit import AuditLogger
from .brokers.base import BrokerClient, OrderRequest, Position
from .config import Settings
from .db import Database
from .enums import Outcome, OrderSide, OrderType
from .models import ClientAccount, ReconciliationSnapshot


@dataclass
class Mismatch:
    client_account_id: str
    instrument: str
    master_qty: Decimal
    client_qty: Decimal

    @property
    def delta(self) -> Decimal:
        return self.master_qty - self.client_qty


@dataclass
class ReconciliationResult:
    checked_clients: int = 0
    mismatches: List[Mismatch] = field(default_factory=list)
    corrected: int = 0
    disabled: int = 0

    @property
    def all_matched(self) -> bool:
        return not self.mismatches


def diff_positions(
    master: Dict[str, Position], client: Dict[str, Position]
) -> Dict[str, Decimal]:
    """Return {instrument: master_net - client_net} for every instrument that
    differs. Pure function — unit-testable without a broker or DB."""
    instruments = set(master) | set(client)
    out: Dict[str, Decimal] = {}
    for sym in instruments:
        m = master[sym].net_quantity if sym in master else Decimal("0")
        c = client[sym].net_quantity if sym in client else Decimal("0")
        if m != c:
            out[sym] = m - c
    return out


class ReconciliationEngine:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        audit: AuditLogger,
        master_broker: BrokerClient,
        client_brokers: Dict[str, BrokerClient],
    ):
        self._db = db
        self._settings = settings
        self._audit = audit
        self._master = master_broker
        self._brokers = client_brokers

    def run_once(self) -> ReconciliationResult:
        result = ReconciliationResult()
        master_positions = self._master.get_positions()

        with self._db.session_scope() as s:
            accounts = s.query(ClientAccount).all()
            account_rows = [
                (a.client_account_id, bool(a.enabled), Decimal(a.copy_ratio))
                for a in accounts
            ]

        for client_id, enabled, copy_ratio in account_rows:
            if not enabled or client_id not in self._brokers:
                continue
            result.checked_clients += 1

            client_positions = self._brokers[client_id].get_positions()
            # Scale the master's book by this follower's copy ratio for a fair
            # comparison.
            expected = {
                sym: Position(sym, p.net_quantity * copy_ratio)
                for sym, p in master_positions.items()
            }
            deltas = diff_positions(expected, client_positions)

            self._snapshot(client_id, expected, client_positions)

            for sym, delta in deltas.items():
                mismatch = Mismatch(
                    client_id, sym,
                    expected.get(sym, Position(sym)).net_quantity,
                    client_positions.get(sym, Position(sym)).net_quantity,
                )
                result.mismatches.append(mismatch)
                self._handle_mismatch(client_id, mismatch, result)

        self._audit.record(
            "ReconciliationEngine",
            "RECONCILIATION_COMPLETE",
            Outcome.SUCCESS if result.all_matched else Outcome.FAILURE,
            {
                "checked_clients": result.checked_clients,
                "mismatches": len(result.mismatches),
                "corrected": result.corrected,
                "disabled": result.disabled,
            },
        )
        return result

    def _handle_mismatch(self, client_id, mismatch: Mismatch, result) -> None:
        if self._settings.reconciliation_auto_correct:
            delta = mismatch.delta
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            self._brokers[client_id].place_order(
                OrderRequest(
                    instrument=mismatch.instrument,
                    side=side,
                    quantity=abs(delta),
                    order_type=OrderType.MARKET,
                )
            )
            result.corrected += 1
            self._audit.record(
                "ReconciliationEngine", "AUTO_CORRECTED", Outcome.SUCCESS,
                {"instrument": mismatch.instrument, "delta": str(delta)},
                client_account_id=client_id,
            )
        else:
            with self._db.session_scope() as s:
                acct = s.get(ClientAccount, client_id)
                acct.enabled = False
            result.disabled += 1
            self._audit.record(
                "ReconciliationEngine", "CLIENT_DISABLED", Outcome.FAILURE,
                {
                    "instrument": mismatch.instrument,
                    "master_qty": str(mismatch.master_qty),
                    "client_qty": str(mismatch.client_qty),
                },
                client_account_id=client_id,
            )

    def _snapshot(self, client_id, expected, client_positions) -> None:
        instruments = set(expected) | set(client_positions)
        with self._db.session_scope() as s:
            for sym in instruments:
                m = expected.get(sym, Position(sym)).net_quantity
                c = client_positions.get(sym, Position(sym)).net_quantity
                s.add(
                    ReconciliationSnapshot(
                        client_account_id=client_id,
                        instrument=sym,
                        master_quantity=m,
                        client_quantity=c,
                        matched=(m == c),
                    )
                )
