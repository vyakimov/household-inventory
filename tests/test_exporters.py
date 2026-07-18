import pytest

from app import db, exporters, mutations


def _q(conn, name, col="quantity"):
    return conn.execute(f"SELECT {col} FROM items WHERE item = ?", (name,)).fetchone()[col]


def test_export_import_roundtrip_unchanged(conn):
    text = exporters.export_csv_string(conn)
    with db.transaction(conn):
        r = exporters.import_csv_string(conn, text)
    assert r["created"] == 0 and r["updated"] == 0 and r["unchanged"] == 6


def test_import_creates_and_updates(conn):
    text = (
        "item,category,quantity,unit,necessity\n"
        "Granola,food,7,units,1\n"
        "Oat milk,food,3,cans,1\n"
    )
    with db.transaction(conn):
        r = exporters.import_csv_string(conn, text)
    assert r == {"created": 1, "updated": 1, "unchanged": 0}
    assert _q(conn, "Granola") == 7
    assert _q(conn, "Oat milk") == 3 and _q(conn, "Oat milk", "necessity") == 1


def test_import_matches_name_case_insensitively(conn):
    with db.transaction(conn):
        r = exporters.import_csv_string(conn, "item,quantity\ngRANOLA,2\n")
    assert r["updated"] == 1 and _q(conn, "Granola") == 2


def test_import_blank_cells_leave_fields_alone(conn):
    with db.transaction(conn):
        exporters.import_csv_string(conn, "item,quantity,unit,notes\nGranola,,,\n")
    row = conn.execute("SELECT * FROM items WHERE item = 'Granola'").fetchone()
    assert row["quantity"] == 1 and row["unit"] == "units"
    # notes was provided (empty) -> explicitly cleared, matching export round-trips
    assert row["notes"] == ""


def test_import_bad_number_reports_line(conn):
    with pytest.raises(mutations.ValidationError, match="line 3"):
        exporters.import_csv_string(conn, "item,quantity\nGranola,2\nSalt,lots\n")


def test_import_unknown_category_reports_line(conn):
    with pytest.raises(mutations.ValidationError, match="line 2"):
        exporters.import_csv_string(conn, "item,category\nNew thing,bogus\n")


def test_import_requires_item_column(conn):
    with pytest.raises(mutations.ValidationError, match="item"):
        exporters.import_csv_string(conn, "name,quantity\nGranola,2\n")


def test_import_rejects_unknown_columns(conn):
    with pytest.raises(mutations.ValidationError, match="quantiy"):
        exporters.import_csv_string(conn, "item,quantiy\nGranola,2\n")


def test_import_events_logged(conn):
    with db.transaction(conn):
        exporters.import_csv_string(conn, "item,category,quantity\nOat milk,food,3\n")
    e = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 1").fetchone()
    assert e["op"] == "create" and e["source"] == "csv-import"


def test_backup_is_single_standalone_file(conn, tmp_path):
    dest = tmp_path / "snap" / "backup.db"
    exporters.backup_to(dest, conn)
    assert dest.exists()
    assert not (dest.parent / "backup.db-wal").exists()
    assert not (dest.parent / "backup.db-shm").exists()
    check = exporters.sqlite3.connect(str(dest))
    try:
        n = check.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        mode = check.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        check.close()
    assert n == 6 and mode != "wal"
