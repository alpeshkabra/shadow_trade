"""Database engine/session management."""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


class Database:
    """Thin wrapper around a SQLAlchemy engine + session factory."""

    def __init__(self, url: str):
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            # ``check_same_thread`` is required because the parallel execution
            # engine touches the DB from a thread pool. A StaticPool keeps a
            # single shared connection so an in-memory database survives across
            # sessions/threads (each pooled connection would otherwise get its
            # own empty ``:memory:`` DB).
            kwargs["connect_args"] = {"check_same_thread": False}
            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._Session()

    @contextmanager
    def session_scope(self):
        """Provide a transactional scope around a series of operations."""
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
