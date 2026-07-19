"""`inv` — agent-facing CLI for the household inventory.

Conventions follow github.com/vyakimov/llm-cli-skill: stdout is a single JSON envelope,
diagnostics go to stderr, errors carry snake_case `error.type` codes, mutations are
dry-runnable, and relative ops can be made idempotent with --request-id.
"""
import argparse
import json
import sys

from . import db, embeddings, mutations, queries

SCHEMA_VERSION = 1

EXIT = {
    "ok": 0,
    "internal_error": 1,
    "resource_not_found": 2,
    "ambiguous_match": 3,
    "invalid_arguments": 4,
    "conflict": 5,
}


# --- envelope helpers ---
def ok(action: str, result, **meta) -> dict:
    return {"ok": True, "action": action, "result": result,
            "meta": {"schema_version": SCHEMA_VERSION, **meta}}


def err(action: str, type_: str, message: str, **details) -> dict:
    return {"ok": False, "action": action,
            "error": {"type": type_, "message": message, "details": details}}


class OpError(Exception):
    def __init__(self, type_, message, **details):
        super().__init__(message)
        self.type = type_
        self.message = message
        self.details = details


# --- resolution ---
def _lookup(conn, item_id, term):
    if item_id is not None:
        it = queries.get_item(conn, item_id)
        return ("ok", [it]) if it else ("not_found", [])
    return queries.resolve(conn, term)


def _resolve_or_raise(conn, item_id, term):
    status, matches = _lookup(conn, item_id, term)
    if status == "ok":
        return matches[0]
    if status == "ambiguous":
        raise OpError("ambiguous_match", f"{len(matches)} items matched {term!r}",
                      query=term, candidates=[{"id": m["id"], "item": m["item"]} for m in matches])
    raise OpError("resource_not_found",
                  f"no item matched {term!r}" if term else f"no item with id {item_id}",
                  query=term, suggestions=_suggestions(conn, term or ""),
                  hint="run `inv catalog` to resolve semantically, then apply with --id")


def _suggestions(conn, term: str) -> list[dict]:
    """Merge optional semantic hints without ever changing resolver behavior."""
    suggestions = queries.suggest(conn, term)
    seen = {row["id"] for row in suggestions}
    for row in embeddings.semantic_search(conn, term, top_k=5):
        if row["id"] not in seen:
            seen.add(row["id"])
            suggestions.append({"id": row["id"], "item": row["item"]})
        if len(suggestions) == 5:
            break
    return suggestions[:5]


def _parse_bool(s) -> bool:
    return str(s).strip().lower() in ("1", "true", "yes", "y", "on")


def _item_arg(args):
    if getattr(args, "id", None) is not None:
        return None
    return getattr(args, "item_option", None) or getattr(args, "item", None)


def _qty_arg(args):
    qty = getattr(args, "qty_option", None)
    if qty is not None:
        return qty
    qty = getattr(args, "qty", None)
    if qty is not None:
        return qty
    # Allows: inv take --item "toilet paper" 1 and inv take --id 3 1
    has_explicit_item_ref = (
        getattr(args, "item_option", None) or getattr(args, "id", None) is not None
    )
    if has_explicit_item_ref and getattr(args, "item", None):
        try:
            return float(args.item)
        except ValueError:
            return None
    return None


def _on_the_way_value(args):
    value = getattr(args, "value_option", None)
    if value is not None:
        return value
    value = getattr(args, "value", None)
    if value is not None:
        return value
    # Allows: inv on-the-way --item "toilet paper" true
    if getattr(args, "item_option", None) or getattr(args, "id", None) is not None:
        return getattr(args, "item", None)
    return None


def _alias_value(args):
    if getattr(args, "value_option", None) is not None:
        return args.value_option
    if getattr(args, "value", None) is not None:
        return args.value
    # Allows: inv alias add --item "toilet paper" TP
    if getattr(args, "item_option", None) or getattr(args, "id", None) is not None:
        return getattr(args, "item", None)
    return None


# --- mutation runner (handles replay, dry-run, learn-alias, unit warning) ---
def _replay(conn, request_id):
    if not request_id:
        return None
    row = conn.execute("SELECT * FROM events WHERE request_id = ?", (request_id,)).fetchone()
    if not row:
        return None
    cur = queries.get_item(conn, row["item_id"]) or {}
    return {
        "id": row["item_id"], "item": row["item_name"],
        "unit": cur.get("unit"), "before": row["qty_before"],
        "delta": row["delta"], "after": row["qty_after"],
        "is_low": bool(cur.get("is_low")), "low_changed": False,
        "on_the_way": bool(cur.get("on_the_way")), "clamped": False,
        "event_id": row["id"],
    }


def _run_mutation(conn, action, args, apply_fn):
    replayed = _replay(conn, getattr(args, "request_id", None))
    if replayed is not None:
        return ok(action, replayed, source=args.source, dry_run=False, idempotent_replay=True)

    try:
        item = _resolve_or_raise(conn, getattr(args, "id", None), _item_arg(args))
    except OpError as e:
        return err(action, e.type, e.message, **e.details)

    warnings = []
    unit = getattr(args, "unit", None)
    if unit and unit != item["unit"]:
        warnings.append(f"requested unit {unit!r} != item unit {item['unit']!r}")
        print(warnings[-1], file=sys.stderr)

    conn.execute("BEGIN")
    try:
        result = apply_fn(conn, item)
        if getattr(args, "learn_alias", None):
            mutations.add_alias(conn, item["id"], args.learn_alias, source=args.source)
            result["learned_alias"] = args.learn_alias
        conn.execute("ROLLBACK" if args.dry_run else "COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    meta = {"source": args.source, "dry_run": bool(args.dry_run)}
    if warnings:
        meta["warnings"] = warnings
    return ok(action, result, **meta)


# --- command handlers ---
def cmd_take(conn, args):
    qty = _qty_arg(args)
    if qty is None or qty <= 0:
        return err("take", "invalid_arguments", "qty must be > 0")
    return _run_mutation(conn, "take", args, lambda c, it: mutations.adjust_quantity(
        c, it["id"], -qty, op="take", source=args.source, note=args.note,
        request_id=args.request_id))


def cmd_put(conn, args):
    qty = _qty_arg(args)
    if qty is None or qty <= 0:
        return err("put", "invalid_arguments", "qty must be > 0")
    return _run_mutation(conn, "put", args, lambda c, it: mutations.adjust_quantity(
        c, it["id"], qty, op="put", source=args.source, note=args.note,
        request_id=args.request_id))


def cmd_set(conn, args):
    qty = _qty_arg(args)
    if qty is None or qty < 0:
        return err("set", "invalid_arguments", "qty must be >= 0")
    return _run_mutation(conn, "set", args, lambda c, it: mutations.set_quantity(
        c, it["id"], qty, source=args.source, note=args.note, request_id=args.request_id))


def cmd_on_the_way(conn, args):
    value = _on_the_way_value(args)
    if value is None:
        return err("on_the_way", "invalid_arguments", "value must be true or false")
    val = _parse_bool(value)
    return _run_mutation(conn, "on_the_way", args, lambda c, it: mutations.set_on_the_way(
        c, it["id"], val, source=args.source, request_id=args.request_id))


def cmd_get(conn, args):
    try:
        item = _resolve_or_raise(conn, args.id, _item_arg(args))
    except OpError as e:
        return err("get", e.type, e.message, **e.details)
    return ok("get", item)


def cmd_search(conn, args):
    query = args.query_option or args.query
    if not query:
        return err("search", "invalid_arguments", "query is required")
    if args.limit <= 0:
        return err("search", "invalid_arguments", "limit must be > 0")
    items, seen = [], set()
    for row in queries.list_items(conn, "all", query):
        if len(items) == args.limit:
            break
        seen.add(row["id"])
        items.append(_search_row(row, "like", None))
    if len(items) < args.limit:
        for match in embeddings.semantic_search(conn, query, top_k=args.limit):
            if match["id"] in seen:
                continue
            row = queries.get_item(conn, match["id"])
            if row is not None:
                seen.add(row["id"])
                items.append(_search_row(row, "semantic", match["score"]))
            if len(items) == args.limit:
                break
    return ok("search", {"items": items, "count": len(items)}, query=query)


def _search_row(row: dict, source: str, score: float | None) -> dict:
    return {key: row[key] for key in ("id", "item", "category", "quantity", "unit")} | {
        "source": source, "score": score,
    }


def cmd_catalog(conn, args):
    items = queries.catalog(conn)
    return ok("catalog", {"items": items, "count": len(items)})


def cmd_list(conn, args):
    items = queries.list_items(conn, args.tab, args.q)
    return ok("list", {"items": items, "count": len(items)}, tab=args.tab)


def cmd_lookups(conn, args):
    return ok("lookups", {"categories": queries.categories(conn), "units": queries.units(conn)})


def cmd_category(conn, args):
    if args.category_cmd == "list":
        rows = conn.execute(
            "SELECT name, sort_order FROM categories ORDER BY sort_order, name"
        ).fetchall()
        return ok("category", {"categories": [dict(row) for row in rows], "count": len(rows)})
    if not args.name:
        return err("category", "invalid_arguments", "category name is required")
    conn.execute("BEGIN")
    try:
        if args.category_cmd == "add":
            result = mutations.add_category(
                conn, args.name, args.sort_order, source=args.source
            )
        else:
            result = mutations.delete_category(conn, args.name, source=args.source)
        conn.execute("ROLLBACK" if args.dry_run else "COMMIT")
    except mutations.ValidationError as e:
        conn.execute("ROLLBACK")
        return err(
            "category",
            "conflict" if "is used by" in str(e) else "invalid_arguments",
            str(e),
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return ok(
        "category", result, source=args.source, dry_run=bool(args.dry_run)
    )


def cmd_log(conn, args):
    where, params = "", []
    item_arg = _item_arg(args)
    if item_arg or args.id is not None:
        try:
            item = _resolve_or_raise(conn, args.id, item_arg)
        except OpError as e:
            return err("log", e.type, e.message, **e.details)
        where, params = "WHERE item_id = ?", [item["id"]]
    rows = conn.execute(
        f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?", [*params, args.limit]
    ).fetchall()
    return ok("log", {"events": [dict(r) for r in rows], "count": len(rows)})


def cmd_new(conn, args):
    fields = {
        "item": args.item, "category": args.category, "unit": args.unit,
        "aliases": args.alias or "", "quantity": args.qty, "step": args.step,
        "low_stock_threshold": args.threshold, "necessity": args.necessity,
        "shopping_item_name": args.shopping_name or "", "notes": args.notes or "",
    }
    conn.execute("BEGIN")
    try:
        item = mutations.create_item(conn, fields, source=args.source)
        conn.execute("ROLLBACK" if args.dry_run else "COMMIT")
    except mutations.ValidationError as e:
        conn.execute("ROLLBACK")
        return err("new", "conflict" if "exists" in str(e) else "invalid_arguments", str(e))
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return ok("new", item, source=args.source, dry_run=bool(args.dry_run))


def cmd_edit(conn, args):
    fields = {k: v for k, v in {
        "item": args.rename, "category": args.category, "unit": args.unit,
        "aliases": args.aliases, "quantity": args.qty, "step": args.step,
        "low_stock_threshold": args.threshold, "shopping_item_name": args.shopping_name,
        "notes": args.notes,
        "necessity": (None if args.necessity is None else _parse_bool(args.necessity)),
    }.items() if v is not None}
    try:
        item = _resolve_or_raise(conn, args.id, _item_arg(args))
    except OpError as e:
        return err("edit", e.type, e.message, **e.details)
    conn.execute("BEGIN")
    try:
        updated = mutations.update_item(conn, item["id"], fields, source=args.source)
        conn.execute("ROLLBACK" if args.dry_run else "COMMIT")
    except mutations.ValidationError as e:
        conn.execute("ROLLBACK")
        return err("edit", "conflict" if "exists" in str(e) else "invalid_arguments", str(e))
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return ok("edit", updated, source=args.source, dry_run=bool(args.dry_run))


def cmd_delete(conn, args):
    try:
        item = _resolve_or_raise(conn, args.id, _item_arg(args))
    except OpError as e:
        return err("delete", e.type, e.message, **e.details)
    conn.execute("BEGIN")
    try:
        removed = mutations.delete_item(conn, item["id"], source=args.source)
        conn.execute("ROLLBACK" if args.dry_run else "COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return ok("delete", {"id": removed["id"], "item": removed["item"]},
              source=args.source, dry_run=bool(args.dry_run))


def cmd_alias(conn, args):
    try:
        item = _resolve_or_raise(conn, args.id, _item_arg(args))
    except OpError as e:
        return err("alias", e.type, e.message, **e.details)
    if args.alias_cmd == "list":
        return ok("alias", {"id": item["id"], "item": item["item"],
                            "aliases": queries.split_aliases(item["aliases"])})
    value = _alias_value(args)
    if not value:
        return err("alias", "invalid_arguments", "alias value is required")
    conn.execute("BEGIN")
    try:
        fn = mutations.add_alias if args.alias_cmd == "add" else mutations.remove_alias
        updated = fn(conn, item["id"], value, source=args.source)
        conn.execute("ROLLBACK" if args.dry_run else "COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return ok("alias", {"id": updated["id"], "item": updated["item"],
                        "aliases": queries.split_aliases(updated["aliases"])},
              dry_run=bool(args.dry_run))


def _batch_qty(op: dict, kind: str) -> float:
    """Required, numeric. A missing qty must never silently default to 0."""
    if "qty" not in op:
        raise OpError("invalid_arguments", f"{kind} requires qty", op=op)
    try:
        return float(op["qty"])
    except (TypeError, ValueError):
        raise OpError("invalid_arguments",
                      f"qty must be a number, got {op['qty']!r}", op=op) from None


def _batch_op(conn, op: dict, source: str) -> dict:
    kind = op.get("op")
    item = _resolve_or_raise(conn, op.get("id"), op.get("item"))
    if kind in ("take", "put", "adjust"):
        qty = _batch_qty(op, kind)
        if qty <= 0:
            raise OpError("invalid_arguments", f"qty must be > 0 for {kind}", op=op)
        delta = -qty if kind == "take" else qty
        return mutations.adjust_quantity(conn, item["id"], delta, op=kind, source=source,
                                         note=op.get("note", ""))
    if kind == "set":
        return mutations.set_quantity(conn, item["id"], _batch_qty(op, "set"),
                                      source=source, note=op.get("note", ""))
    if kind == "on_the_way":
        if "value" not in op:
            raise OpError("invalid_arguments", "on_the_way requires value", op=op)
        return mutations.set_on_the_way(conn, item["id"], _parse_bool(op["value"]), source=source)
    if kind == "categorize":
        category = (op.get("category") or "").strip()
        if not category:
            raise OpError("invalid_arguments", "categorize requires category", op=op)
        return mutations.update_item(conn, item["id"], {"category": category}, source=source)
    if kind == "alias_add":
        alias = (op.get("alias") or "").strip()
        if not alias:
            raise OpError("invalid_arguments", "alias_add requires alias", op=op)
        return mutations.add_alias(conn, item["id"], alias, source=source)
    raise OpError("invalid_arguments", f"unknown op {kind!r}", op=op)


def cmd_batch(conn, args):
    try:
        ops = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        return err("batch", "invalid_arguments", f"stdin is not valid JSON: {e}")
    if not isinstance(ops, list):
        return err("batch", "invalid_arguments", "expected a JSON array of ops")
    results = []
    conn.execute("BEGIN")
    try:
        for i, op in enumerate(ops):
            try:
                results.append(_batch_op(conn, op, args.source))
            except OpError as e:
                conn.execute("ROLLBACK")
                return err("batch", e.type, f"op {i}: {e.message}", index=i, **e.details)
            except mutations.ValidationError as e:
                conn.execute("ROLLBACK")
                return err("batch", "invalid_arguments", f"op {i}: {e}", index=i)
        conn.execute("ROLLBACK" if args.dry_run else "COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return ok("batch", {"results": results, "count": len(results)},
              source=args.source, dry_run=bool(args.dry_run))


ACTIONS = [
    {"name": "take", "summary": "Decrease quantity (clamped at 0)", "params": ["item|--item|--id", "qty|--qty", "--unit", "--request-id", "--learn-alias", "--dry-run"]},
    {"name": "put", "summary": "Increase quantity", "params": ["item|--item|--id", "qty|--qty", "--unit", "--request-id", "--learn-alias", "--dry-run"]},
    {"name": "set", "summary": "Set exact quantity", "params": ["item|--item|--id", "qty|--qty", "--request-id", "--dry-run"]},
    {"name": "on-the-way", "summary": "Set the on-the-way flag", "params": ["item|--item|--id", "true|false|--value"]},
    {"name": "get", "summary": "Show one item's current state", "params": ["item|--item|--id"]},
    {"name": "search", "summary": "Name/alias search with semantic fallback", "params": ["query|--query", "--limit"]},
    {"name": "catalog", "summary": "Dump all items+aliases for semantic resolution", "params": []},
    {"name": "list", "summary": "List items by filter tab", "params": ["--tab low|necessities|needs-buy|all", "--q"]},
    {"name": "lookups", "summary": "List valid categories and units", "params": []},
    {"name": "category", "summary": "Add, remove, or list category lookups", "params": ["add|rm|list", "name", "--sort-order", "--dry-run"]},
    {"name": "new", "summary": "Create an item", "params": ["item", "--category", "--unit", "--qty", "--threshold", "--necessity", "--step", "--alias"]},
    {"name": "edit", "summary": "Edit item fields", "params": ["item|--item|--id", "--rename", "--category", "--unit", "--qty", "--threshold", "--necessity", "--aliases", "--step"]},
    {"name": "delete", "summary": "Delete an item", "params": ["item|--item|--id", "--dry-run"]},
    {"name": "alias", "summary": "add|rm|list aliases", "params": ["add|rm|list", "item|--item|--id", "value|--value"]},
    {"name": "batch", "summary": "Apply a JSON array of ops atomically (stdin)", "params": ["--dry-run", "--source"]},
    {"name": "log", "summary": "Recent change events", "params": ["item|--item|--id", "--limit"]},
    {"name": "list-actions", "summary": "This list", "params": []},
]


def build_parser() -> argparse.ArgumentParser:
    # default=SUPPRESS so these may appear before OR after the subcommand without the
    # subparser's copy clobbering a value the top-level parser already parsed.
    g = argparse.ArgumentParser(add_help=False)
    g.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS,
                   help="indent JSON output")
    g.add_argument("--db", default=argparse.SUPPRESS,
                   help="path to the SQLite database (overrides default)")

    # shared options for quantity-style mutations
    m = argparse.ArgumentParser(add_help=False)
    m.add_argument("item", nargs="?", help="item name or alias")
    m.add_argument("--item", dest="item_option", help="item name or alias")
    m.add_argument("--id", type=int, help="resolve by item id instead of name")
    m.add_argument("--source", default="cli", help="event source tag (e.g. agent)")
    m.add_argument("--note", default="", help="note recorded in the event log")
    m.add_argument("--request-id", dest="request_id", help="idempotency key (dedup via events)")
    m.add_argument("--dry-run", dest="dry_run", action="store_true")

    p = argparse.ArgumentParser(prog="inv", parents=[g])
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("take", "put", "set"):
        sp = sub.add_parser(name, parents=[g, m])
        sp.add_argument("qty", nargs="?", type=float)
        sp.add_argument("--qty", dest="qty_option", type=float)
        if name != "set":
            sp.add_argument("--unit")
            sp.add_argument("--learn-alias", dest="learn_alias")

    sp = sub.add_parser("on-the-way", parents=[g, m])
    sp.add_argument("value", nargs="?", help="true/false")
    sp.add_argument("--value", dest="value_option", help="true/false")

    sp = sub.add_parser("get", parents=[g])
    sp.add_argument("item", nargs="?")
    sp.add_argument("--item", dest="item_option")
    sp.add_argument("--id", type=int)

    sp = sub.add_parser("search", parents=[g])
    sp.add_argument("query", nargs="?")
    sp.add_argument("--query", dest="query_option")
    sp.add_argument("--limit", type=int, default=8)

    sub.add_parser("catalog", parents=[g])

    sp = sub.add_parser("list", parents=[g])
    sp.add_argument("--tab", choices=["low", "necessities", "needs-buy", "all"], default="all")
    sp.add_argument("--q")

    sub.add_parser("lookups", parents=[g])

    sp = sub.add_parser("category", parents=[g])
    sp.add_argument("category_cmd", choices=["add", "rm", "list"])
    sp.add_argument("name", nargs="?")
    sp.add_argument("--sort-order", type=int)
    sp.add_argument("--source", default="cli")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")

    sp = sub.add_parser("new", parents=[g])
    sp.add_argument("item")
    sp.add_argument("--category", required=True)
    sp.add_argument("--unit", default="units")
    sp.add_argument("--qty", type=float, default=0)
    sp.add_argument("--step", type=float, default=1)
    sp.add_argument("--threshold", type=float, default=0)
    sp.add_argument("--necessity", action="store_true")
    sp.add_argument("--alias")
    sp.add_argument("--shopping-name", dest="shopping_name")
    sp.add_argument("--notes")
    sp.add_argument("--source", default="cli")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")

    sp = sub.add_parser("edit", parents=[g])
    sp.add_argument("item", nargs="?")
    sp.add_argument("--item", dest="item_option")
    sp.add_argument("--id", type=int)
    sp.add_argument("--rename")
    sp.add_argument("--category")
    sp.add_argument("--unit")
    sp.add_argument("--qty", type=float)
    sp.add_argument("--step", type=float)
    sp.add_argument("--threshold", type=float)
    sp.add_argument("--necessity")
    sp.add_argument("--aliases")
    sp.add_argument("--shopping-name", dest="shopping_name")
    sp.add_argument("--notes")
    sp.add_argument("--source", default="cli")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")

    sp = sub.add_parser("delete", parents=[g])
    sp.add_argument("item", nargs="?")
    sp.add_argument("--item", dest="item_option")
    sp.add_argument("--id", type=int)
    sp.add_argument("--source", default="cli")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")

    sp = sub.add_parser("alias", parents=[g])
    sp.add_argument("alias_cmd", choices=["add", "rm", "list"])
    sp.add_argument("item", nargs="?")
    sp.add_argument("value", nargs="?", help="the alias (for add/rm)")
    sp.add_argument("--item", dest="item_option")
    sp.add_argument("--value", dest="value_option", help="the alias (for add/rm)")
    sp.add_argument("--id", type=int)
    sp.add_argument("--source", default="cli")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")

    sp = sub.add_parser("batch", parents=[g])
    sp.add_argument("--source", default="cli")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")

    sp = sub.add_parser("log", parents=[g])
    sp.add_argument("item", nargs="?")
    sp.add_argument("--item", dest="item_option")
    sp.add_argument("--id", type=int)
    sp.add_argument("--limit", type=int, default=20)

    sub.add_parser("list-actions", parents=[g])
    return p


HANDLERS = {
    "take": cmd_take, "put": cmd_put, "set": cmd_set, "on-the-way": cmd_on_the_way,
    "get": cmd_get, "search": cmd_search, "catalog": cmd_catalog, "list": cmd_list,
    "lookups": cmd_lookups, "category": cmd_category,
    "new": cmd_new, "edit": cmd_edit, "delete": cmd_delete, "alias": cmd_alias,
    "batch": cmd_batch, "log": cmd_log,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "list-actions":
        env = ok("list-actions", {"actions": ACTIONS})
    else:
        conn = None
        try:
            conn = db.connect(getattr(args, "db", None))
            env = HANDLERS[args.cmd](conn, args)
        except mutations.ValidationError as e:
            env = err(args.cmd, "invalid_arguments", str(e))
        except Exception as e:  # noqa: BLE001 - surface as a clean envelope
            env = err(args.cmd, "internal_error", f"{type(e).__name__}: {e}")
        finally:
            if conn is not None:
                conn.close()

    print(json.dumps(env, indent=2 if getattr(args, "pretty", False) else None))
    code = 0 if env["ok"] else EXIT.get(env["error"]["type"], 1)
    return code


if __name__ == "__main__":
    sys.exit(main())
