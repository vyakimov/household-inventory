"""CSV export and SQLite backup helpers (shared by web routes and scripts)."""
import csv
import io
import sqlite3
from pathlib import Path

from . import db

CSV_FIELDS = [
    "item", "aliases", "category", "quantity", "unit", "step",
    "low_stock_threshold", "necessity", "on_the_way", "shopping_item_name", "notes",
]


def export_csv_string(conn: sqlite3.Connection) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=CSV_FIELDS)
    w.writeheader()
    cols = ", ".join(CSV_FIELDS)
    for r in conn.execute(f"SELECT {cols} FROM items ORDER BY category, item COLLATE NOCASE"):
        w.writerow({k: r[k] for k in CSV_FIELDS})
    return out.getvalue()


def backup_to(dest: str | Path, conn: sqlite3.Connection | None = None) -> Path:
    """Consistent online backup via the SQLite backup API."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = conn or db.connect()
    target = sqlite3.connect(str(dest))
    try:
        with target:
            src.backup(target)
    finally:
        target.close()
        if conn is None:
            src.close()
    return dest
