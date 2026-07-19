"""ASGI entrypoint: ``uvicorn shadow_trade.asgi:app``.

Boots a CopyTradingEngine backed by simulated brokers and a couple of demo
followers, then exposes the management API. Swap ``SimulatedBroker`` for a real
broker adapter to run against live accounts.
"""

from decimal import Decimal

from .api import create_app
from .brokers import SimulatedBroker
from .config import load_settings
from .engine import CopyTradingEngine

_PRICES = {"RELIANCE": Decimal("2900"), "TCS": Decimal("3850"), "INFY": Decimal("1650")}

_master = SimulatedBroker("MASTER", _PRICES)
_followers = {
    "FOLLOWER_A": SimulatedBroker("A", dict(_PRICES)),
    "FOLLOWER_B": SimulatedBroker("B", dict(_PRICES)),
}

engine = CopyTradingEngine(_master, _followers, settings=load_settings())
engine.register_client("FOLLOWER_A", "Alice", Decimal("1000000"))
engine.register_client("FOLLOWER_B", "Bob", Decimal("1000000"), copy_ratio=Decimal("2"))

app = create_app(engine)
