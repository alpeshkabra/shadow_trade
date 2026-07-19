"""Domain enumerations for the Copy Trading Engine.

Values mirror the states defined in the HLD (docs/HLD.md).
"""

from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class EventType(str, Enum):
    NEW = "NEW"
    MODIFY = "MODIFY"
    CANCEL = "CANCEL"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"
    REJECTION = "REJECTION"


class MasterStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class ExecutionStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    SLIPPAGE_REJECTED = "SLIPPAGE_REJECTED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    FAILED = "FAILED"


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    SKIPPED = "SKIPPED"
    REJECTED = "REJECTED"
