"""Database setup.

SQLite is used for local demonstration. All tables use portable SQLAlchemy types
(String, Integer, Float, DateTime, JSON) so the same models run on PostgreSQL by
setting ``DATABASE_URL=postgresql+psycopg://...``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_URL = "sqlite:///" + os.path.join(os.path.dirname(os.path.dirname(__file__)), "governance.db")


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def configure(url: str | None = None) -> None:
    """(Re)configure the engine. Tests call this with an isolated database."""
    global _engine, _SessionLocal
    url = url or os.environ.get("DATABASE_URL", DEFAULT_URL)
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    _engine = create_engine(url, future=True, **kwargs)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def engine():
    if _engine is None:
        configure()
    return _engine


def create_all() -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine())


def drop_all() -> None:
    from app import models  # noqa: F401

    Base.metadata.drop_all(engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    if _SessionLocal is None:
        configure()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    gen = get_session()
    session = next(gen)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
