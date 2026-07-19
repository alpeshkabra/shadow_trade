"""In-memory simulated broker (paper trading).

Lets the whole engine run end-to-end with no real broker connection — used by
the demo and the test suite. It models fills at the prevailing market price
plus a configurable per-instrument slippage so the SlippageGuard has something
realistic to evaluate.
"""

import itertools
import threading
from decimal import Decimal
from typing import Dict

from ..enums import OrderSide
from .base import BrokerClient, OrderRequest, OrderResult, Position


class SimulatedBroker(BrokerClient):
    def __init__(self, name: str, prices: Dict[str, Decimal] | None = None):
        self.name = name
        self._prices: Dict[str, Decimal] = dict(prices or {})
        self._positions: Dict[str, Position] = {}
        self._order_seq = itertools.count(1)
        self._lock = threading.Lock()
        # Per-instrument execution offset (as a fraction, e.g. 0.005 = +0.5%),
        # used to simulate realistic slippage against the reference price.
        self._exec_offset: Dict[str, Decimal] = {}
        # If set, the next N orders are rejected — used to exercise retries.
        self._fail_next = 0

    # --- test/demo controls ------------------------------------------------
    def set_price(self, instrument: str, price: Decimal) -> None:
        self._prices[instrument] = Decimal(price)

    def set_exec_offset(self, instrument: str, offset: Decimal) -> None:
        self._exec_offset[instrument] = Decimal(offset)

    def fail_next_orders(self, n: int) -> None:
        self._fail_next = n

    # --- BrokerClient ------------------------------------------------------
    def last_price(self, instrument: str) -> Decimal:
        return self._prices.get(instrument, Decimal("100"))

    def place_order(self, order: OrderRequest) -> OrderResult:
        with self._lock:
            if self._fail_next > 0:
                self._fail_next -= 1
                raise ConnectionError("simulated transient broker failure")

            ref = self.last_price(order.instrument)
            offset = self._exec_offset.get(order.instrument, Decimal("0"))
            fill_price = (ref * (Decimal("1") + offset)).quantize(Decimal("0.0001"))

            pos = self._positions.setdefault(
                order.instrument, Position(order.instrument, Decimal("0"))
            )
            signed = order.quantity if order.side == OrderSide.BUY else -order.quantity
            pos.net_quantity += signed

            return OrderResult(
                broker_order_id=f"{self.name}-{next(self._order_seq)}",
                instrument=order.instrument,
                side=order.side,
                quantity=order.quantity,
                fill_price=fill_price,
                status="FILLED",
            )

    def get_positions(self) -> Dict[str, Position]:
        with self._lock:
            return {
                sym: Position(sym, p.net_quantity)
                for sym, p in self._positions.items()
                if p.net_quantity != 0
            }
