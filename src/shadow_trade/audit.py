"""Append-only audit logger (HLD section 4.7).

Every meaningful decision the engine makes is written to ``audit_log`` with a
globally unique event id, so any master trade can be fully reconstructed.
"""

import uuid
from typing import Optional

from .db import Database
from .enums import Outcome
from .models import AuditLog


class AuditLogger:
    def __init__(self, db: Database):
        self._db = db

    def record(
        self,
        component: str,
        event_type: str,
        outcome: Outcome,
        payload: dict,
        master_trade_id: Optional[str] = None,
        client_account_id: Optional[str] = None,
    ) -> None:
        with self._db.session_scope() as s:
            s.add(
                AuditLog(
                    event_id=str(uuid.uuid4()),
                    component=component,
                    event_type=event_type,
                    outcome=outcome.value,
                    payload=payload,
                    master_trade_id=master_trade_id,
                    client_account_id=client_account_id,
                )
            )
