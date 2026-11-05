"""Database engine/session handling. Schema is managed by Alembic (alembic upgrade head);
for the zero-config demo the tables are created at startup if missing (SQLite)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import get_settings
from .models import Base, DataRetentionPolicy

_engine = None
_Session = None


def engine():
    global _engine, _Session
    if _engine is None:
        s = get_settings()
        kw = {"pool_pre_ping": True}
        if s.database_url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        _engine = create_engine(s.database_url, **kw)
        if s.database_url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _fk(dbapi_conn, _):  # pragma: no cover
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db(create: bool = True):
    eng = engine()
    if create:
        Base.metadata.create_all(eng)
    with session() as db:
        if not db.query(DataRetentionPolicy).first():
            for t, d in (("raw_samples", 30), ("quality_assessments", 30), ("reconciled_samples", 90), ("audit_events", 3650),
                         ("mpc_recommendations", 3650), ("alarms", 3650), ("observer_snapshots", 90)):
                db.add(DataRetentionPolicy(table_name=t, retain_days=d, soft_delete=False))


@contextmanager
def session() -> Iterator[Session]:
    engine()
    db = _Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
