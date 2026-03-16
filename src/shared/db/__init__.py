"""SQLAlchemy engine, session factory, schema migrations, and LangGraph checkpointer.

Reads DATABASE_URL from env. Supports sqlite:/// and postgresql:// schemes.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

_CHECKPOINT_SERDE = JsonPlusSerializer(
    allowed_json_modules=(
        ("shared.models", "RawDocument"),
        ("shared.models", "Transaction"),
    ),
)

_engine = None
_SessionFactory: sessionmaker[Session] | None = None
_checkpoint_conn: sqlite3.Connection | None = None
_pg_checkpointer: Any = None


def get_database_url() -> str:
    """Resolve DATABASE_URL from environment; defaults to sqlite:///data/agent.db."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    Path("data").mkdir(parents=True, exist_ok=True)
    return "sqlite:///data/agent.db"


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _sa_url(url: str) -> str:
    """Add SQLAlchemy dialect prefix for psycopg v3."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def get_engine() -> Any:
    """Return the shared SQLAlchemy engine (created once)."""
    global _engine
    if _engine is None:
        url = get_database_url()
        if _is_sqlite(url):
            _engine = create_engine(
                url, echo=False, connect_args={"check_same_thread": False}
            )

            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn: Any, _record: Any) -> None:
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.close()
        else:
            _engine = create_engine(_sa_url(url), echo=False, pool_pre_ping=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the shared session factory."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory


def get_session() -> Session:
    """Create and return a new SQLAlchemy session (caller owns lifecycle)."""
    return get_session_factory()()


def run_migrations() -> None:
    """Run pending Alembic migrations (called at app startup)."""
    from alembic import command
    from alembic.config import Config

    # alembic.ini lives at the project root (three levels above this file)
    project_root = Path(__file__).resolve().parents[3]
    alembic_ini = project_root / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("sqlalchemy.url", _sa_url(get_database_url()))
    # Override script_location with absolute path so it works from any CWD
    alembic_cfg.set_main_option(
        "script_location", str(project_root / "src" / "shared" / "db" / "alembic")
    )
    command.upgrade(alembic_cfg, "head")


def get_checkpointer() -> Any:
    """Return a LangGraph checkpointer backed by the configured database.

    SQLite: dedicated raw sqlite3 connection (avoids concurrent commit conflicts).
    PostgreSQL: PostgresSaver from langgraph-checkpoint-postgres.
    """
    global _checkpoint_conn, _pg_checkpointer
    url = get_database_url()
    if _is_sqlite(url):
        if _checkpoint_conn is None:
            db_path = url.replace("sqlite:///", "")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            _checkpoint_conn = sqlite3.connect(db_path, check_same_thread=False)
            _checkpoint_conn.execute("PRAGMA journal_mode=WAL;")
        return SqliteSaver(_checkpoint_conn, serde=_CHECKPOINT_SERDE)
    else:
        if _pg_checkpointer is None:
            import psycopg
            from psycopg.rows import dict_row
            from langgraph.checkpoint.postgres import PostgresSaver
            conn = psycopg.connect(url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
            _pg_checkpointer = PostgresSaver(conn)
            _pg_checkpointer.setup()
        return _pg_checkpointer
