"""One-time importer: pull the live Notion inventory into SQLite.

Reads NOTION_TOKEN from the shared .env (see app/settings.py). Uses the Notion REST
API directly (the MCP query path is plan-gated). Recomputed formulas (Low stock,
Need to buy) are not imported; v_items derives them. The Notion `Need to buy` value is
read only to reconcile against the app's computed count.

Run:  uv run python scripts/import_from_notion.py [--dry-run]
"""
import argparse
import sys

import httpx

from app import db, settings

API = "https://api.notion.com/v1"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.NOTION_TOKEN}",
        "Notion-Version": settings.NOTION_VERSION,
        "Content-Type": "application/json",
    }


def fetch_pages() -> list[dict]:
    """Page through the Notion database and return all row objects."""
    url = f"{API}/databases/{settings.NOTION_DATABASE_ID}/query"
    pages, cursor = [], None
    with httpx.Client(timeout=30) as client:
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            r = client.post(url, headers=_headers(), json=body)
            if r.status_code == 401:
                sys.exit("Notion auth failed (401): check NOTION_TOKEN.")
            if r.status_code == 404:
                sys.exit(
                    "Notion database not found (404): share the integration with the "
                    f"'Household Inventory Items' database ({settings.NOTION_DATABASE_ID})."
                )
            r.raise_for_status()
            data = r.json()
            pages.extend(data["results"])
            if not data.get("has_more"):
                break
            cursor = data["next_cursor"]
    return pages


# --- Notion property extractors (tolerant of missing properties) ---
def _title(props, name):
    return "".join(t["plain_text"] for t in props.get(name, {}).get("title", [])).strip()


def _text(props, name):
    return "".join(t["plain_text"] for t in props.get(name, {}).get("rich_text", [])).strip()


def _select(props, name):
    sel = props.get(name, {}).get("select")
    return sel["name"] if sel else None


def _number(props, name):
    return props.get(name, {}).get("number")


def _checkbox(props, name):
    return bool(props.get(name, {}).get("checkbox"))


def _formula_bool(props, name):
    f = props.get(name, {}).get("formula", {})
    v = f.get(f.get("type"))
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v)


def parse_row(page: dict) -> dict | None:
    p = page["properties"]
    item = _title(p, "Item")
    if not item:
        return None
    return {
        "item": item,
        "aliases": _text(p, "Aliases"),
        "category": _select(p, "Category"),
        "quantity": _number(p, "Quantity") or 0,
        "unit": _select(p, "Unit"),
        "low_stock_threshold": _number(p, "Low stock threshold") or 0,
        "necessity": 1 if _checkbox(p, "Necessity") else 0,
        "on_the_way": 1 if _checkbox(p, "On the way") else 0,
        "shopping_item_name": _text(p, "Shopping item name"),
        "notion_need_to_buy": _formula_bool(p, "Need to buy"),
    }


def ensure_lookups(conn, rows) -> None:
    with db.transaction(conn):
        for cat in {r["category"] for r in rows if r["category"]} | {settings.FALLBACK_CATEGORY}:
            conn.execute(
                "INSERT OR IGNORE INTO categories(name, sort_order) VALUES (?, 99)", (cat,)
            )
        for unit in {r["unit"] for r in rows if r["unit"]}:
            conn.execute("INSERT OR IGNORE INTO units(name) VALUES (?)", (unit,))


def upsert(conn, rows) -> None:
    sql = """
        INSERT INTO items
            (item, aliases, category, quantity, unit, low_stock_threshold,
             necessity, on_the_way, shopping_item_name)
        VALUES (:item, :aliases, :category, :quantity, :unit, :low_stock_threshold,
                :necessity, :on_the_way, :shopping_item_name)
        ON CONFLICT(item) DO UPDATE SET
            aliases             = excluded.aliases,
            category            = excluded.category,
            quantity            = excluded.quantity,
            unit                = excluded.unit,
            low_stock_threshold = excluded.low_stock_threshold,
            necessity           = excluded.necessity,
            on_the_way          = excluded.on_the_way,
            shopping_item_name  = excluded.shopping_item_name,
            updated_at          = datetime('now')
    """  # step/notes are intentionally preserved on update (local-only fields).
    with db.transaction(conn):
        for r in rows:
            conn.execute(
                sql,
                {
                    **{k: r[k] for k in (
                        "item", "aliases", "quantity", "low_stock_threshold",
                        "necessity", "on_the_way", "shopping_item_name",
                    )},
                    "category": r["category"] or settings.FALLBACK_CATEGORY,
                    "unit": r["unit"] or settings.DEFAULT_UNIT,
                },
            )


def reconcile(conn, rows) -> None:
    notion_need = {r["item"] for r in rows if r["notion_need_to_buy"]}
    app_need = {
        row["item"] for row in conn.execute("SELECT item FROM v_items WHERE needs_buy = 1")
    }
    print("\nReconciliation — need to buy:")
    print(f"  Notion formula count: {len(notion_need)}")
    print(f"  App computed count:   {len(app_need)}")
    if notion_need == app_need:
        print("  ✓ match")
        return
    only_notion = sorted(notion_need - app_need)
    only_app = sorted(app_need - notion_need)
    print("  ⚠ mismatch (expected if threshold semantics differ):")
    if only_notion:
        print(f"    only Notion says buy: {only_notion}")
    if only_app:
        print(f"    only app says buy:    {only_app}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch and report without writing")
    args = ap.parse_args()

    if not settings.NOTION_TOKEN:
        sys.exit("NOTION_TOKEN not set (see .env.example).")
    if not settings.NOTION_DATABASE_ID:
        sys.exit("NOTION_DATABASE_ID not set (see .env.example).")

    print("Fetching Notion rows…")
    pages = fetch_pages()
    rows = [r for r in (parse_row(p) for p in pages) if r]
    print(f"  fetched {len(pages)} pages, {len(rows)} items with a name")

    conn = db.connect()
    if args.dry_run:
        reconcile_preview = sum(1 for r in rows if r["notion_need_to_buy"])
        print(f"[dry-run] would import {len(rows)} items; "
              f"Notion need-to-buy count = {reconcile_preview}")
        conn.close()
        return

    ensure_lookups(conn, rows)
    upsert(conn, rows)
    total = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    print(f"  imported/updated {len(rows)} items; {total} total in DB")
    reconcile(conn, rows)
    conn.close()


if __name__ == "__main__":
    main()
