"""CopyTradingEngine — composition root that wires all components together
(HLD section 3). This is the object the API and the demo drive.
"""

from decimal import Decimal
from typing import Dict, List

from .audit import AuditLogger
from .brokers.base import BrokerClient
from .config import Settings, load_settings
from .db import Database
from .detector import MasterTradeDetector
from .enums import EventType, MasterStatus, OrderSide, OrderType
from .kill_switch import KillSwitchController
from .models import ClientAccount
from .orchestrator import ClientOutcome, DistributionOrchestrator
from .reconciliation import ReconciliationEngine, ReconciliationResult


class CopyTradingEngine:
    def __init__(
        self,
        master_broker: BrokerClient,
        client_brokers: Dict[str, BrokerClient],
        settings: Settings | None = None,
    ):
        self.settings = settings or load_settings()
        self.db = Database(self.settings.database_url)
        self.db.create_all()

        self.master_broker = master_broker
        self.client_brokers = client_brokers

        self.audit = AuditLogger(self.db)
        self.kill_switch = KillSwitchController(self.db)
        self.detector = MasterTradeDetector(self.db, self.audit)
        self.orchestrator = DistributionOrchestrator(
            self.db, self.settings, self.kill_switch, self.audit, client_brokers
        )
        self.reconciler = ReconciliationEngine(
            self.db, self.settings, self.audit, master_broker, client_brokers
        )

    # -- account management -------------------------------------------------
    def register_client(
        self,
        client_account_id: str,
        name: str,
        available_margin: Decimal,
        copy_ratio: Decimal = Decimal("1"),
        max_open_trades: int = 100,
    ) -> None:
        with self.db.session_scope() as s:
            if s.get(ClientAccount, client_account_id) is None:
                s.add(
                    ClientAccount(
                        client_account_id=client_account_id,
                        name=name,
                        available_margin=Decimal(available_margin),
                        copy_ratio=Decimal(copy_ratio),
                        max_open_trades=max_open_trades,
                        enabled=True,
                    )
                )

    # -- core flow ----------------------------------------------------------
    def on_master_trade(
        self,
        master_trade_id: str,
        instrument: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        event_type: EventType = EventType.NEW,
        order_type: OrderType = OrderType.MARKET,
        event_seq: int = 0,
    ) -> List[ClientOutcome]:
        """Ingest a master trade and copy it to all eligible clients."""
        event = self.detector.ingest(
            master_trade_id, instrument, side, quantity, price,
            event_type=event_type, order_type=order_type,
            status=MasterStatus.FILLED, event_seq=event_seq,
        )
        return self.orchestrator.distribute(event)

    def reconcile(self) -> ReconciliationResult:
        return self.reconciler.run_once()
