"""Kill Switch Controller (HLD section 4.6).

Emergency halt for all copy execution. Backed by the database so the state
survives a restart, with an in-memory cache for the hot path.
"""

from typing import Optional

from .db import Database
from .models import KillSwitchState


class KillSwitchController:
    def __init__(self, db: Database):
        self._db = db
        self._cached: Optional[bool] = None

    def _row(self, s) -> KillSwitchState:
        row = s.query(KillSwitchState).order_by(KillSwitchState.id.asc()).first()
        if row is None:
            row = KillSwitchState(active=False)
            s.add(row)
            s.flush()
        return row

    def is_active(self) -> bool:
        if self._cached is not None:
            return self._cached
        with self._db.session_scope() as s:
            self._cached = bool(self._row(s).active)
        return self._cached

    def activate(self, reason: str) -> None:
        with self._db.session_scope() as s:
            row = self._row(s)
            row.active = True
            row.reason = reason
        self._cached = True

    def deactivate(self) -> None:
        with self._db.session_scope() as s:
            row = self._row(s)
            row.active = False
            row.reason = None
        self._cached = False

    def state(self) -> dict:
        with self._db.session_scope() as s:
            row = self._row(s)
            return {"active": bool(row.active), "reason": row.reason}
