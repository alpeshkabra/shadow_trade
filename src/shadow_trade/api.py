"""Internal management API (HLD section 12).

A FastAPI app exposing health, kill-switch, client, reconciliation, trade and
audit endpoints over the running engine. Build it with ``create_app(engine)``.
"""

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .engine import CopyTradingEngine
from .models import (
    AuditLog,
    ClientAccount,
    ClientExecutionLog,
    MasterTradeEvent,
)


class KillSwitchRequest(BaseModel):
    active: bool
    reason: Optional[str] = None


def create_app(engine: CopyTradingEngine) -> FastAPI:
    app = FastAPI(title="ShadowTrade Copy Trading Engine", version="1.0.0")

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "kill_switch": engine.kill_switch.state(),
            "clients": len(engine.client_brokers),
            "reconciliation_engine": "running",
        }

    @app.get("/api/v1/kill-switch")
    def get_kill_switch():
        return engine.kill_switch.state()

    @app.post("/api/v1/kill-switch")
    def set_kill_switch(req: KillSwitchRequest):
        if req.active:
            engine.kill_switch.activate(req.reason or "manual")
        else:
            engine.kill_switch.deactivate()
        return engine.kill_switch.state()

    @app.get("/api/v1/clients")
    def list_clients():
        with engine.db.session_scope() as s:
            rows = s.query(ClientAccount).all()
            return [
                {
                    "client_account_id": r.client_account_id,
                    "name": r.name,
                    "enabled": bool(r.enabled),
                    "copy_ratio": str(r.copy_ratio),
                    "available_margin": str(r.available_margin),
                    "max_open_trades": r.max_open_trades,
                }
                for r in rows
            ]

    @app.get("/api/v1/reconciliation/status")
    def reconciliation_status():
        from .models import ReconciliationSnapshot

        with engine.db.session_scope() as s:
            rows = (
                s.query(ReconciliationSnapshot)
                .order_by(ReconciliationSnapshot.id.desc())
                .limit(50)
                .all()
            )
            return [
                {
                    "client_account_id": r.client_account_id,
                    "instrument": r.instrument,
                    "master_quantity": str(r.master_quantity),
                    "client_quantity": str(r.client_quantity),
                    "matched": bool(r.matched),
                    "timestamp": r.timestamp.isoformat(),
                }
                for r in rows
            ]

    @app.post("/api/v1/reconciliation/run")
    def run_reconciliation():
        result = engine.reconcile()
        return {
            "checked_clients": result.checked_clients,
            "mismatches": len(result.mismatches),
            "corrected": result.corrected,
            "disabled": result.disabled,
            "all_matched": result.all_matched,
        }

    @app.get("/api/v1/trades/active")
    def active_trades():
        with engine.db.session_scope() as s:
            rows = (
                s.query(MasterTradeEvent)
                .filter(MasterTradeEvent.status.in_(["OPEN", "PARTIAL", "PENDING"]))
                .all()
            )
            return [
                {
                    "master_trade_id": r.master_trade_id,
                    "instrument": r.instrument,
                    "side": r.side,
                    "quantity": str(r.quantity),
                    "status": r.status,
                }
                for r in rows
            ]

    @app.get("/api/v1/audit/{master_trade_id}")
    def audit_trail(master_trade_id: str):
        with engine.db.session_scope() as s:
            rows = (
                s.query(AuditLog)
                .filter(AuditLog.master_trade_id == master_trade_id)
                .order_by(AuditLog.id.asc())
                .all()
            )
            if not rows:
                # Also surface client executions even if no audit rows matched.
                execs = (
                    s.query(ClientExecutionLog)
                    .filter(ClientExecutionLog.master_trade_id == master_trade_id)
                    .count()
                )
                if execs == 0:
                    raise HTTPException(404, f"No records for {master_trade_id}")
            return [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "component": r.component,
                    "event_type": r.event_type,
                    "outcome": r.outcome,
                    "client_account_id": r.client_account_id,
                    "payload": r.payload,
                }
                for r in rows
            ]

    return app
