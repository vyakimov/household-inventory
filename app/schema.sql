-- Household inventory schema (SQLite).
-- Run via scripts/init_db.py, which also seeds the lookup tables.

CREATE TABLE IF NOT EXISTS categories (
    name        TEXT PRIMARY KEY,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS units (
    name TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS items (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    item                 TEXT NOT NULL UNIQUE COLLATE NOCASE,
    aliases              TEXT NOT NULL DEFAULT '',
    category             TEXT NOT NULL REFERENCES categories(name),
    quantity             REAL NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    unit                 TEXT NOT NULL DEFAULT 'units' REFERENCES units(name),
    step                 REAL NOT NULL DEFAULT 1 CHECK (step > 0),
    low_stock_threshold  REAL NOT NULL DEFAULT -1
                         CHECK (low_stock_threshold = -1 OR low_stock_threshold >= 0),
    necessity            INTEGER NOT NULL DEFAULT 0 CHECK (necessity IN (0, 1)),
    on_the_way           INTEGER NOT NULL DEFAULT 0 CHECK (on_the_way IN (0, 1)),
    shopping_item_name   TEXT NOT NULL DEFAULT '',
    notes                TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);
CREATE INDEX IF NOT EXISTS idx_items_item ON items(item);

-- Derived semantic-search cache. It is maintained outside mutations/events.
CREATE TABLE IF NOT EXISTS item_embeddings (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    text_hash  TEXT NOT NULL,
    vector     BLOB NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Computed flags over every row; the filter tabs are WHERE clauses on this view.
-- A threshold of -1 disables restocking; 0 means restock when empty.
DROP VIEW IF EXISTS v_items;
CREATE VIEW v_items AS
SELECT
    i.*,
    CAST(i.necessity = 1 AND i.low_stock_threshold >= 0
         AND i.quantity <= i.low_stock_threshold AS INTEGER) AS is_low,
    CAST(i.necessity = 1 AND i.low_stock_threshold >= 0
         AND i.quantity <= i.low_stock_threshold
         AND i.on_the_way = 0 AS INTEGER) AS needs_buy
FROM items i;

-- Append-only audit log written by every web and CLI mutation.
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER REFERENCES items(id) ON DELETE SET NULL,
    item_name   TEXT NOT NULL,
    op          TEXT NOT NULL,
    delta       REAL,
    qty_before  REAL,
    qty_after   REAL,
    source      TEXT NOT NULL DEFAULT 'cli',
    note        TEXT NOT NULL DEFAULT '',
    request_id  TEXT UNIQUE,            -- NULLs are distinct; set for idempotent CLI calls
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_item ON events(item_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
