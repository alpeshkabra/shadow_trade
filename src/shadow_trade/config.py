"""Twelve-factor configuration (environment variables with sane defaults).

See HLD section 11 (Technology Stack) and section 4.3 (Slippage Guard).
"""

import os
from dataclasses import dataclass
from decimal import Decimal


def _env_decimal(key: str, default: str) -> Decimal:
    return Decimal(os.getenv(key, default))


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


@dataclass(frozen=True)
class Settings:
    # Database — defaults to a local SQLite file so the engine runs out of the
    # box; point at PostgreSQL in production (see HLD section 11).
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///shadow_trade.db")

    # Maximum tolerated price deviation between the master fill price and the
    # client execution price. HLD 4.3: "price deviation < 2%".
    max_slippage_pct: Decimal = _env_decimal("MAX_SLIPPAGE_PCT", "2.0")

    # Reconciliation cadence in seconds (HLD 4.5).
    reconciliation_interval_s: int = _env_int("RECONCILIATION_INTERVAL_S", 60)

    # Parallel execution fan-out across client accounts (HLD 4.4).
    max_execution_workers: int = _env_int("MAX_EXECUTION_WORKERS", 8)

    # Retry policy for transient broker failures (HLD 8.2).
    max_order_retries: int = _env_int("MAX_ORDER_RETRIES", 3)

    # If True, reconciliation auto-corrects drift; otherwise it disables the
    # drifting client (HLD 4.5).
    reconciliation_auto_correct: bool = (
        os.getenv("RECONCILIATION_AUTO_CORRECT", "false").lower() == "true"
    )


def load_settings() -> Settings:
    return Settings()
