"""Broker abstraction.

The engine talks to brokers only through this interface, so a real broker
adapter (Zerodha, Angel One, etc.) can be dropped in without touching the
orchestration logic. See HLD section 2.2 (External Interfaces).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Optional

from ..enums import OrderSide, OrderType


@dataclass
class OrderRequest:
    instrument: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    price: Optional[Decimal] = None


@dataclass
class OrderResult:
    broker_order_id: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    fill_price: Decimal
    status: str  # FILLED / REJECTED
    reason: Optional[str] = None


@dataclass
class Position:
    instrument: str
    net_quantity: Decimal = field(default_factory=lambda: Decimal(0))


class BrokerClient(ABC):
    """Interface every broker adapter must implement."""

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        ...

    @abstractmethod
    def get_positions(self) -> Dict[str, Position]:
        """Return net positions keyed by instrument symbol."""
        ...

    @abstractmethod
    def last_price(self, instrument: str) -> Decimal:
        ...
