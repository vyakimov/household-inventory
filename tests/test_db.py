from app import db


def test_init_db_migrates_legacy_zero_thresholds_to_disabled(tmp_path):
    path = tmp_path / "legacy.db"
    conn = db.connect(path)
    legacy_schema = db.SCHEMA_PATH.read_text().replace(
        "low_stock_threshold  REAL NOT NULL DEFAULT -1\n"
        "                         CHECK (low_stock_threshold = -1 OR low_stock_threshold >= 0)",
        "low_stock_threshold  REAL NOT NULL DEFAULT 0 CHECK (low_stock_threshold >= 0)",
    ).replace("i.low_stock_threshold >= 0", "i.low_stock_threshold > 0")
    conn.executescript(legacy_schema)
    conn.execute("INSERT INTO categories(name) VALUES ('food')")
    conn.execute("INSERT INTO units(name) VALUES ('units')")
    conn.execute(
        "INSERT INTO items(item, category, unit, necessity, low_stock_threshold) "
        "VALUES ('Salt', 'food', 'units', 1, 0)"
    )
    item_id = conn.execute("SELECT id FROM items WHERE item = 'Salt'").fetchone()["id"]
    conn.execute(
        "INSERT INTO events(item_id, item_name, op) VALUES (?, 'Salt', 'legacy')",
        (item_id,),
    )

    db.init_db(conn)

    item = conn.execute("SELECT * FROM v_items WHERE id = ?", (item_id,)).fetchone()
    assert item["low_stock_threshold"] == -1
    assert item["is_low"] == 0 and item["needs_buy"] == 0
    assert conn.execute("SELECT item_id FROM events").fetchone()["item_id"] == item_id
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'items'"
    ).fetchone()["sql"]
    assert "low_stock_threshold = -1" in table_sql
    conn.close()
