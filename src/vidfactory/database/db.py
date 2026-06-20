from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from vidfactory.config import get_config

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _split_statements(sql: str) -> list[str]:
    """Split a .sql file into individual statements (SQLite executes one at a time).

    Splits on ';' and drops chunks that contain only comments/whitespace. The schema files
    contain no ';' inside string literals, so a plain split is safe here.
    """
    statements: list[str] = []
    for chunk in sql.split(";"):
        has_sql = any(
            line.strip() and not line.strip().startswith("--")
            for line in chunk.splitlines()
        )
        if has_sql:
            statements.append(chunk.strip())
    return statements

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        db_path = get_config().db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, future=True)
    return _engine


def init_db() -> None:
    """Enable WAL and apply any pending .sql migrations (tracked in _migrations)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON;")
        _run_migrations(conn)


def _run_migrations(conn) -> None:
    conn.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS _migrations (filename TEXT PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {row[0] for row in conn.exec_driver_sql("SELECT filename FROM _migrations")}

    applied_count = 0
    skipped_count = 0
    for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in applied:
            skipped_count += 1
            continue
        logger.info("Applying migration %s", sql_file.name)
        for statement in _split_statements(sql_file.read_text(encoding="utf-8")):
            conn.exec_driver_sql(statement)
        conn.exec_driver_sql(
            "INSERT INTO _migrations (filename) VALUES (?)", (sql_file.name,)
        )
        applied_count += 1

    logger.info("Migrations applied: %d (skipped: %d)", applied_count, skipped_count)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session."""
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
