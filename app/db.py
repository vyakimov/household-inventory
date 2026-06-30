"""SQLite connection helpers and transaction management."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import settings

SCHEMA_PATH = settings.BASE_DIR / "app" / "schema.sql"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a short-lived connection. Autocommit mode; use transaction() to batch writes."""
    path = str(db_path) if db_path is not None else str(settings.DB_PATH)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables, indexes, and the view (idempotent)."""
    conn.executescript(SCHEMA_PATH.read_text())


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Explicit transaction; commits on success, rolls back on exception."""
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
