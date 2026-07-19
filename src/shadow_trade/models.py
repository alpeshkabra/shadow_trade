"""SQLAlchemy models mirroring the data architecture in HLD section 5.

Uses portable column types (Numeric, JSON) so the same schema runs on SQLite
for local development and PostgreSQL in production.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.utcnow()


class ClientAccount(Base):
    __tablename__ = "client_accounts"

    client_account_id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    # Copy sizing: multiplier applied to master quantity for this follower.
    copy_ratio = Column(Numeric(10, 4), nullable=False, default=1)
    available_margin = Column(Numeric(18, 4), nullable=False, default=0)
    max_open_trades = Column(Integer, nullable=False, default=100)
    created_at = Column(DateTime, default=_utcnow)


class MasterTradeEvent(Base):
    __tablename__ = "master_trade_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    master_trade_id = Column(String(64), nullable=False, index=True)
    event_type = Column(String(32), nullable=False)
    instrument = Column(String(32), nullable=False)
    side = Column(String(4), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=False)
    order_type = Column(String(16), nullable=False)
    price = Column(Numeric(18, 4), nullable=True)
    filled_quantity = Column(Numeric(18, 4), nullable=False, default=0)
    status = Column(String(32), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=_utcnow)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class ClientExecutionLog(Base):
    __tablename__ = "client_execution_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # `{client_account_id}:{master_trade_id}:{event_seq}` — the idempotency key.
    execution_id = Column(String(128), unique=True, nullable=False, index=True)
    master_trade_id = Column(String(64), nullable=False, index=True)
    client_account_id = Column(String(64), nullable=False, index=True)
    instrument = Column(String(32), nullable=False)
    side = Column(String(4), nullable=False)
    quantity = Column(Numeric(18, 4), nullable=False)
    order_type = Column(String(16), nullable=False)
    price = Column(Numeric(18, 4), nullable=True)
    broker_order_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, index=True)
    slippage_pct = Column(Numeric(8, 4), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    error_detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(128), unique=True, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=_utcnow)
    component = Column(String(64), nullable=False)
    event_type = Column(String(64), nullable=False)
    master_trade_id = Column(String(64), nullable=True, index=True)
    client_account_id = Column(String(64), nullable=True, index=True)
    payload = Column(JSON, nullable=False)
    outcome = Column(String(32), nullable=False)


class ReconciliationSnapshot(Base):
    __tablename__ = "reconciliation_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=_utcnow)
    client_account_id = Column(String(64), nullable=False, index=True)
    instrument = Column(String(32), nullable=False)
    master_quantity = Column(Numeric(18, 4), nullable=False)
    client_quantity = Column(Numeric(18, 4), nullable=False)
    matched = Column(Boolean, nullable=False)


class KillSwitchState(Base):
    __tablename__ = "kill_switch_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    active = Column(Boolean, nullable=False, default=False)
    reason = Column(String(256), nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
