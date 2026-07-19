"""Master Trade Detector (HLD section 4.1).

Ingests order events from the master account (in production, off a broker
WebSocket; here, fed directly), persists them to ``master_trade_events`` as the
durable event store, and normalizes them into a ``MasterEvent`` for the
orchestrator. The persisted row id doubles as the monotonic ``event_seq`` used
to build idempotency keys.
"""

from decimal import Decimal

from .audit import AuditLogger
from .db import Database
from .enums import EventType, MasterStatus, Outcome, OrderSide, OrderType
from .models import MasterTradeEvent
from .orchestrator import MasterEvent


class MasterTradeDetector:
    def __init__(self, db: Database, audit: AuditLogger):
        self._db = db
        self._audit = audit

    def ingest(
        self,
        master_trade_id: str,
        instrument: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        event_type: EventType = EventType.NEW,
        order_type: OrderType = OrderType.MARKET,
        status: MasterStatus = MasterStatus.FILLED,
        event_seq: int = 0,
        raw_payload: dict | None = None,
    ) -> MasterEvent:
        # ``event_seq`` is the stable logical sequence of this event within the
        # master order (0 for the initial NEW; increment for each partial fill).
        # It — not the DB row id — forms the idempotency key, so replaying the
        # same event is correctly detected as a duplicate downstream.
        with self._db.session_scope() as s:
            row = MasterTradeEvent(
                master_trade_id=master_trade_id,
                event_type=event_type.value,
                instrument=instrument,
                side=side.value,
                quantity=Decimal(quantity),
                order_type=order_type.value,
                price=Decimal(price),
                filled_quantity=Decimal(quantity) if status == MasterStatus.FILLED else Decimal("0"),
                status=status.value,
                raw_payload=raw_payload or {},
            )
            s.add(row)
            s.flush()

        self._audit.record(
            "MasterTradeDetector", f"MASTER_{event_type.value}", Outcome.SUCCESS,
            {
                "master_trade_id": master_trade_id,
                "instrument": instrument,
                "side": side.value,
                "quantity": str(quantity),
                "price": str(price),
            },
            master_trade_id=master_trade_id,
        )

        return MasterEvent(
            master_trade_id=master_trade_id,
            event_seq=event_seq,
            event_type=event_type,
            instrument=instrument,
            side=side,
            quantity=Decimal(quantity),
            order_type=order_type,
            price=Decimal(price),
        )
