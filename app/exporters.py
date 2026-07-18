"""CSV import/export and SQLite backup helpers (shared by web routes and scripts)."""
import csv
import io
import sqlite3
from pathlib import Path

from . import db, mutations

CSV_FIELDS = [
    "item", "aliases", "category", "quantity", "unit", "step",
    "low_stock_threshold", "necessity", "on_the_way", "shopping_item_name", "notes",
]

_NUMERIC_FIELDS = ("quantity", "step", "low_stock_threshold")
_BOOL_FIELDS = ("necessity", "on_the_way")
_TRUE = {"1", "true", "yes", "y", "on"}


def export_csv_string(conn: sqlite3.Connection) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=CSV_FIELDS)
    w.writeheader()
    cols = ", ".join(CSV_FIELDS)
    for r in conn.execute(f"SELECT {cols} FROM items ORDER BY category, item COLLATE NOCASE"):
        w.writerow({k: r[k] for k in CSV_FIELDS})
    return out.getvalue()


def _typed_fields(raw: dict, line: int) -> dict:
    """Typed fields from one CSV row; absent/blank cells are omitted (left unchanged)."""
    cells = {k: v.strip() for k, v in raw.items() if k in CSV_FIELDS and v is not None}
    fields: dict = {}
    for k in ("item", "category", "unit"):
        if cells.get(k):
            fields[k] = cells[k]
    for k in ("aliases", "shopping_item_name", "notes"):
        if k in cells:  # empty string is meaningful: clears the field
            fields[k] = cells[k]
    for k in _NUMERIC_FIELDS:
        if cells.get(k):
            try:
                fields[k] = float(cells[k])
            except ValueError:
                raise mutations.ValidationError(
                    f"line {line}: {k} must be a number, got {cells[k]!r}") from None
    for k in _BOOL_FIELDS:
        if cells.get(k):
            fields[k] = 1 if cells[k].lower() in _TRUE else 0
    return fields


def import_csv_string(conn: sqlite3.Connection, text: str, *, source="csv-import") -> dict:
    """Upsert items from CSV text (the export format). Caller wraps in a transaction;
    a ValidationError on any row aborts the whole import."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "item" not in reader.fieldnames:
        raise mutations.ValidationError("CSV needs a header row with at least an 'item' column")
    # a typo'd header must not silently no-op the column (empty names from trailing commas are fine)
    unknown = [c for c in reader.fieldnames if c and c not in CSV_FIELDS]
    if unknown:
        raise mutations.ValidationError(
            "unknown column(s) " + ", ".join(repr(c) for c in unknown)
            + " — valid: " + ", ".join(CSV_FIELDS))
    created = updated = unchanged = 0
    for line, raw in enumerate(reader, start=2):
        fields = _typed_fields(raw, line)
        name = fields.get("item")
        if not name:
            raise mutations.ValidationError(f"line {line}: item name is required")
        existing = conn.execute(
            "SELECT * FROM items WHERE item = ? COLLATE NOCASE", (name,)).fetchone()
        try:
            if existing is None:
                mutations.create_item(conn, fields, source=source)
                created += 1
            else:
                changes = {k: v for k, v in fields.items()
                           if k != "item" and v != existing[k]}
                if changes:
                    mutations.update_item(conn, existing["id"], changes, source=source)
                    updated += 1
                else:
                    unchanged += 1
        except mutations.ValidationError as e:
            raise mutations.ValidationError(f"line {line}: {e}") from None
    return {"created": created, "updated": updated, "unchanged": unchanged}


def backup_to(dest: str | Path, conn: sqlite3.Connection | None = None) -> Path:
    """Consistent online backup via the SQLite backup API."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = conn or db.connect()
    target = sqlite3.connect(str(dest))
    try:
        with target:
            src.backup(target)
        # drop WAL mode so the backup is one standalone file (no -wal/-shm strays)
        target.execute("PRAGMA journal_mode = DELETE")
    finally:
        target.close()
        if conn is None:
            src.close()
    return dest
