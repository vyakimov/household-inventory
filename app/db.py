"""SQLite connection helpers and transaction management."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import settings

SCHEMA_PATH = settings.BASE_DIR / "app" / "schema.sql"


def _migrate_threshold_sentinel(conn: sqlite3.Connection) -> None:
    """Change the legacy >=0 threshold constraint, preserving disabled zeroes as -1."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'items'"
    ).fetchone()
    if row is None or "CHECK (low_stock_threshold >= 0)" not in row["sql"]:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(
            """
            BEGIN IMMEDIATE;
            DROP VIEW IF EXISTS v_items;
            CREATE TABLE items_new (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                item                 TEXT NOT NULL UNIQUE COLLATE NOCASE,
                aliases              TEXT NOT NULL DEFAULT '',
                category             TEXT NOT NULL REFERENCES categories(name),
                quantity             REAL NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                unit                 TEXT NOT NULL DEFAULT 'units' REFERENCES units(name),
                step                 REAL NOT NULL DEFAULT 1 CHECK (step > 0),
                low_stock_threshold  REAL NOT NULL DEFAULT -1
                                     CHECK (low_stock_threshold = -1
                                            OR low_stock_threshold >= 0),
                necessity            INTEGER NOT NULL DEFAULT 0
                                     CHECK (necessity IN (0, 1)),
                on_the_way           INTEGER NOT NULL DEFAULT 0
                                     CHECK (on_the_way IN (0, 1)),
                shopping_item_name   TEXT NOT NULL DEFAULT '',
                notes                TEXT NOT NULL DEFAULT '',
                created_at           TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO items_new (
                id, item, aliases, category, quantity, unit, step,
                low_stock_threshold, necessity, on_the_way, shopping_item_name,
                notes, created_at, updated_at
            )
            SELECT
                id, item, aliases, category, quantity, unit, step,
                CASE low_stock_threshold WHEN 0 THEN -1 ELSE low_stock_threshold END,
                necessity, on_the_way, shopping_item_name, notes, created_at, updated_at
            FROM items;
            DROP TABLE items;
            ALTER TABLE items_new RENAME TO items;
            COMMIT;
            """
        )
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


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
    _migrate_threshold_sentinel(conn)
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
