"""Write operations. Every mutation logs an event row.

These functions assume they run inside a transaction (the caller opens one, which is
what makes `inv batch` atomic). Use db.transaction(conn) around single calls.
"""
import sqlite3

from . import queries

VALID_FIELDS = {
    "item", "aliases", "category", "quantity", "unit", "step",
    "low_stock_threshold", "necessity", "on_the_way", "shopping_item_name", "notes",
}


class ValidationError(ValueError):
    """Raised on invalid input (bad enum, negative quantity, missing item, ...)."""


def _row(conn: sqlite3.Connection, item_id: int) -> dict:
    r = conn.execute("SELECT * FROM v_items WHERE id = ?", (item_id,)).fetchone()
    if r is None:
        raise ValidationError(f"no item with id {item_id}")
    return dict(r)


def _log(conn, *, item_id, item_name, op, delta=None, qty_before=None,
         qty_after=None, source="cli", note="", request_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO events(item_id, item_name, op, delta, qty_before, qty_after,"
        " source, note, request_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (item_id, item_name, op, delta, qty_before, qty_after, source, note, request_id),
    )
    return cur.lastrowid


def _result(before: dict, after: dict, *, event_id: int, clamped: bool = False) -> dict:
    return {
        "id": after["id"],
        "item": after["item"],
        "unit": after["unit"],
        "before": before["quantity"],
        "delta": after["quantity"] - before["quantity"],
        "after": after["quantity"],
        "is_low": bool(after["is_low"]),
        "low_changed": bool(after["is_low"]) != bool(before["is_low"]),
        "on_the_way": bool(after["on_the_way"]),
        "clamped": clamped,
        "event_id": event_id,
    }


def _validate_category(conn, name):
    if not conn.execute("SELECT 1 FROM categories WHERE name = ?", (name,)).fetchone():
        raise ValidationError(f"unknown category '{name}'")


def _validate_unit(conn, name):
    if not conn.execute("SELECT 1 FROM units WHERE name = ?", (name,)).fetchone():
        raise ValidationError(f"unknown unit '{name}'")


def add_category(conn, name, sort_order=None, *, source="cli") -> dict:
    """Register a category lookup value, returning the existing row if present."""
    name = (name or "").strip()
    if not name:
        raise ValidationError("category name is required")
    existing = conn.execute(
        "SELECT name, sort_order FROM categories WHERE name = ?", (name,)
    ).fetchone()
    if existing:
        if sort_order is None or int(sort_order) == existing["sort_order"]:
            return {**dict(existing), "created": False, "updated": False}
        sort_order = int(sort_order)
        conn.execute(
            "UPDATE categories SET sort_order = ? WHERE name = ?",
            (sort_order, name),
        )
        _log(
            conn,
            item_id=None,
            item_name=name,
            op="category_reorder",
            source=source,
            note=f"sort_order={sort_order}",
        )
        return {"name": name, "sort_order": sort_order, "created": False, "updated": True}
    if sort_order is None:
        sort_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories"
        ).fetchone()[0]
    sort_order = int(sort_order)
    conn.execute(
        "INSERT INTO categories(name, sort_order) VALUES (?, ?)",
        (name, sort_order),
    )
    _log(
        conn,
        item_id=None,
        item_name=name,
        op="category_add",
        source=source,
        note=f"sort_order={sort_order}",
    )
    return {"name": name, "sort_order": sort_order, "created": True, "updated": False}


def delete_category(conn, name, *, source="cli") -> dict:
    """Delete an unused category lookup value."""
    name = (name or "").strip()
    row = conn.execute(
        "SELECT name, sort_order FROM categories WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        raise ValidationError(f"unknown category '{name}'")
    used = conn.execute(
        "SELECT COUNT(*) FROM items WHERE category = ?", (name,)
    ).fetchone()[0]
    if used:
        raise ValidationError(f"category '{name}' is used by {used} item(s)")
    conn.execute("DELETE FROM categories WHERE name = ?", (name,))
    _log(
        conn,
        item_id=None,
        item_name=name,
        op="category_delete",
        source=source,
    )
    return {"name": name, "sort_order": row["sort_order"], "deleted": True}


def adjust_quantity(conn, item_id, delta, *, op="adjust", source="cli",
                    note="", request_id=None) -> dict:
    """Add `delta` (signed) to quantity, clamped at 0."""
    before = _row(conn, item_id)
    raw = before["quantity"] + delta
    after_qty = max(0.0, raw)
    conn.execute(
        "UPDATE items SET quantity = ?, updated_at = datetime('now') WHERE id = ?",
        (after_qty, item_id),
    )
    after = _row(conn, item_id)
    eid = _log(conn, item_id=item_id, item_name=before["item"], op=op,
               delta=after_qty - before["quantity"], qty_before=before["quantity"],
               qty_after=after_qty, source=source, note=note, request_id=request_id)
    return _result(before, after, event_id=eid, clamped=raw < 0)


def set_quantity(conn, item_id, value, *, source="cli", note="", request_id=None) -> dict:
    if value < 0:
        raise ValidationError("quantity must be >= 0")
    before = _row(conn, item_id)
    conn.execute(
        "UPDATE items SET quantity = ?, updated_at = datetime('now') WHERE id = ?",
        (float(value), item_id),
    )
    after = _row(conn, item_id)
    eid = _log(conn, item_id=item_id, item_name=before["item"], op="set",
               delta=after["quantity"] - before["quantity"], qty_before=before["quantity"],
               qty_after=after["quantity"], source=source, note=note, request_id=request_id)
    return _result(before, after, event_id=eid)


def set_on_the_way(conn, item_id, value, *, source="cli", request_id=None) -> dict:
    before = _row(conn, item_id)
    v = 1 if value else 0
    conn.execute(
        "UPDATE items SET on_the_way = ?, updated_at = datetime('now') WHERE id = ?",
        (v, item_id),
    )
    after = _row(conn, item_id)
    eid = _log(conn, item_id=item_id, item_name=before["item"], op="on_the_way",
               qty_before=before["quantity"], qty_after=after["quantity"],
               source=source, note=f"on_the_way={bool(v)}", request_id=request_id)
    return _result(before, after, event_id=eid)


def add_alias(conn, item_id, alias, *, source="cli") -> dict:
    before = _row(conn, item_id)
    existing = queries.split_aliases(before["aliases"])
    alias = alias.strip()
    if queries.ALIAS_SEP.search(alias):
        # A separator would silently split into several aliases on the next read,
        # bypassing the cross-item collision check below.
        raise ValidationError(f"alias '{alias}' must not contain ',' or ';'")
    if not alias or alias.casefold() == before["item"].casefold():
        return before
    if alias.casefold() in (a.casefold() for a in existing):
        return before
    for row in conn.execute("SELECT id, item, aliases FROM items WHERE id != ?", (item_id,)):
        labels = [row["item"], *queries.split_aliases(row["aliases"])]
        if alias.casefold() in (label.casefold() for label in labels):
            raise ValidationError(
                f"alias '{alias}' already identifies item '{row['item']}'"
            )
    existing.append(alias)
    conn.execute(
        "UPDATE items SET aliases = ?, updated_at = datetime('now') WHERE id = ?",
        (", ".join(existing), item_id),
    )
    _log(conn, item_id=item_id, item_name=before["item"], op="alias_add",
         source=source, note=f"+alias '{alias}'")
    return _row(conn, item_id)


def remove_alias(conn, item_id, alias, *, source="cli") -> dict:
    before = _row(conn, item_id)
    kept = [a for a in queries.split_aliases(before["aliases"]) if a.lower() != alias.strip().lower()]
    conn.execute(
        "UPDATE items SET aliases = ?, updated_at = datetime('now') WHERE id = ?",
        (", ".join(kept), item_id),
    )
    _log(conn, item_id=item_id, item_name=before["item"], op="alias_rm",
         source=source, note=f"-alias '{alias}'")
    return _row(conn, item_id)


def update_item(conn, item_id, fields: dict, *, source="cli") -> dict:
    before = _row(conn, item_id)
    updates = {k: v for k, v in fields.items() if k in VALID_FIELDS and v is not None}
    if not updates:
        return before
    if "item" in updates:
        name = (updates["item"] or "").strip()
        if not name:
            raise ValidationError("item name is required")
        if conn.execute("SELECT 1 FROM items WHERE item = ? COLLATE NOCASE AND id != ?",
                        (name, item_id)).fetchone():
            raise ValidationError(f"item '{name}' already exists")
        updates["item"] = name
    if "category" in updates:
        _validate_category(conn, updates["category"])
    if "unit" in updates:
        _validate_unit(conn, updates["unit"])
    if "quantity" in updates and updates["quantity"] < 0:
        raise ValidationError("quantity must be >= 0")
    if "step" in updates and updates["step"] <= 0:
        raise ValidationError("step must be > 0")
    for b in ("necessity", "on_the_way"):
        if b in updates:
            updates[b] = 1 if updates[b] else 0
    cols = ", ".join(f"{k} = :{k}" for k in updates)
    conn.execute(
        f"UPDATE items SET {cols}, updated_at = datetime('now') WHERE id = :id",
        {**updates, "id": item_id},
    )
    after = _row(conn, item_id)
    _log(conn, item_id=item_id, item_name=after["item"], op="edit",
         qty_before=before["quantity"], qty_after=after["quantity"],
         source=source, note="fields: " + ",".join(updates))
    return after


def create_item(conn, fields: dict, *, source="cli") -> dict:
    name = (fields.get("item") or "").strip()
    if not name:
        raise ValidationError("item name is required")
    category = fields.get("category")
    if not category:
        raise ValidationError("category is required")
    _validate_category(conn, category)
    unit = fields.get("unit") or "units"
    _validate_unit(conn, unit)
    if conn.execute("SELECT 1 FROM items WHERE item = ? COLLATE NOCASE", (name,)).fetchone():
        raise ValidationError(f"item '{name}' already exists")
    row = {
        "item": name,
        "aliases": fields.get("aliases", ""),
        "category": category,
        "quantity": float(fields.get("quantity", 0) or 0),
        "unit": unit,
        "step": float(fields.get("step", 1) or 1),
        "low_stock_threshold": float(fields.get("low_stock_threshold", 0) or 0),
        "necessity": 1 if fields.get("necessity") else 0,
        "on_the_way": 1 if fields.get("on_the_way") else 0,
        "shopping_item_name": fields.get("shopping_item_name", ""),
        "notes": fields.get("notes", ""),
    }
    cur = conn.execute(
        "INSERT INTO items (item, aliases, category, quantity, unit, step,"
        " low_stock_threshold, necessity, on_the_way, shopping_item_name, notes)"
        " VALUES (:item,:aliases,:category,:quantity,:unit,:step,:low_stock_threshold,"
        ":necessity,:on_the_way,:shopping_item_name,:notes)",
        row,
    )
    item_id = cur.lastrowid
    after = _row(conn, item_id)
    _log(conn, item_id=item_id, item_name=name, op="create",
         qty_after=after["quantity"], source=source)
    return after


def delete_item(conn, item_id, *, source="cli") -> dict:
    before = _row(conn, item_id)
    _log(conn, item_id=item_id, item_name=before["item"], op="delete",
         qty_before=before["quantity"], source=source)
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return before
