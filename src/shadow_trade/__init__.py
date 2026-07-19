"""ShadowTrade — a high-reliability copy-trading engine for swing trading.

Public entry points:
    from shadow_trade import CopyTradingEngine, SimulatedBroker
"""

from .brokers import BrokerClient, OrderRequest, OrderResult, Position, SimulatedBroker
from .config import Settings, load_settings
from .engine import CopyTradingEngine
from .enums import EventType, ExecutionStatus, OrderSide, OrderType

__version__ = "0.1.0"

__all__ = [
    "CopyTradingEngine",
    "SimulatedBroker",
    "BrokerClient",
    "OrderRequest",
    "OrderResult",
    "Position",
    "Settings",
    "load_settings",
    "OrderSide",
    "OrderType",
    "EventType",
    "ExecutionStatus",
]
