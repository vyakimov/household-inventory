"""FastAPI app: phone-first inventory UI with HTMX partials. No auth (LAN-only, v1)."""
import html
import os
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import db, embeddings, exporters, mutations, queries, settings

app = FastAPI(title="Household Inventory")
app.mount("/static", StaticFiles(directory=settings.BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(settings.BASE_DIR / "app" / "templates"))
templates.env.filters["num"] = lambda v: ("%g" % float(v)) if v is not None else ""

TABS = ["low", "needs-buy", "necessities", "all"]


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _inventory_ctx(request, conn, tab, q):
    tab = tab if tab in TABS else "low"
    items, semantic = _search_items(conn, tab, q)
    return {
        "request": request,
        "tab": tab,
        "q": q or "",
        "groups": queries.group_by_category(items),
        "low_count": queries.count_low(conn),
        "buy_count": queries.count_needs_buy(conn),
        "categories": queries.categories(conn),
        "units": queries.units(conn),
        "semantic": semantic,
    }


def _search_items(conn, tab: str, q: str | None) -> tuple[list[dict], bool]:
    """Use semantic matches only after the normal name/alias search misses."""
    items = queries.list_items(conn, tab, q)
    if items or not q or not embeddings.enabled():
        return items, False
    matches = embeddings.semantic_search(conn, q)
    if not matches:
        return [], False
    ids = [match["id"] for match in matches]
    placeholders = ", ".join("?" for _ in ids)
    rows = {
        row["id"]: dict(row)
        for row in conn.execute(f"SELECT * FROM v_items WHERE id IN ({placeholders})", ids)
    }
    def in_tab(item: dict) -> bool:
        return (
            tab == "all"
            or (tab == "low" and item["is_low"])
            or (tab == "necessities" and item["necessity"])
            or (tab == "needs-buy" and item["needs_buy"])
        )
    items = [rows[item_id] for item_id in ids if item_id in rows and in_tab(rows[item_id])]
    return items, bool(items)


def _get_or_404(conn, item_id: int) -> dict:
    item = queries.get_item(conn, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"no item with id {item_id}")
    return item


def _card(request, conn, item_id, *, low_changed: bool = False):
    item = _get_or_404(conn, item_id)
    resp = templates.TemplateResponse(request, "partials/item_card.html", {"i": item})
    if low_changed:
        # lets the inventory page refilter live (card leaves the Low tab, counts update)
        resp.headers["HX-Trigger"] = "low-changed"
    return resp


# --- pages ---
@app.get("/", response_class=HTMLResponse)
def index(request: Request, tab: str = "low", q: str | None = None, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "inventory.html", _inventory_ctx(request, conn, tab, q))


@app.get("/partials/inventory", response_class=HTMLResponse)
def partial_inventory(request: Request, tab: str = "low", q: str | None = None, conn=Depends(get_conn)):
    # oob=True piggybacks the header low-count onto out-of-band partial refreshes
    ctx = {**_inventory_ctx(request, conn, tab, q), "oob": True}
    return templates.TemplateResponse(request, "partials/inventory_body.html", ctx)


@app.get("/partials/list", response_class=HTMLResponse)
def partial_list(request: Request, tab: str = "all", q: str | None = None, conn=Depends(get_conn)):
    tab = tab if tab in TABS else "all"
    items, semantic = _search_items(conn, tab, q)
    return templates.TemplateResponse(request, "partials/item_list.html",
                                      {"request": request, "q": q or "", "semantic": semantic,
                                       "groups": queries.group_by_category(items)})


@app.get("/partials/item/{item_id}", response_class=HTMLResponse)
def partial_item(request: Request, item_id: int, conn=Depends(get_conn)):
    return _card(request, conn, item_id)


# --- card mutations (return the refreshed card) ---
@app.post("/items/{item_id}/inc", response_class=HTMLResponse)
def item_inc(request: Request, item_id: int, conn=Depends(get_conn)):
    item = _get_or_404(conn, item_id)
    with db.transaction(conn):
        r = mutations.adjust_quantity(conn, item_id, item["step"], op="put", source="web")
    return _card(request, conn, item_id, low_changed=r["low_changed"])


@app.post("/items/{item_id}/dec", response_class=HTMLResponse)
def item_dec(request: Request, item_id: int, conn=Depends(get_conn)):
    item = _get_or_404(conn, item_id)
    with db.transaction(conn):
        r = mutations.adjust_quantity(conn, item_id, -item["step"], op="take", source="web")
    return _card(request, conn, item_id, low_changed=r["low_changed"])


@app.post("/items/{item_id}/quantity", response_class=HTMLResponse)
def item_set_quantity(request: Request, item_id: int, quantity: float = Form(...), conn=Depends(get_conn)):
    _get_or_404(conn, item_id)
    with db.transaction(conn):
        r = mutations.set_quantity(conn, item_id, max(0.0, quantity), source="web")
    return _card(request, conn, item_id, low_changed=r["low_changed"])


@app.post("/items/{item_id}/on-the-way", response_class=HTMLResponse)
def item_on_the_way(request: Request, item_id: int, value: int = Form(...), conn=Depends(get_conn)):
    _get_or_404(conn, item_id)
    with db.transaction(conn):
        mutations.set_on_the_way(conn, item_id, bool(value), source="web")
    return _card(request, conn, item_id)


# --- history ---
def _event_line(e: dict) -> str:
    """One human-readable line per audit event."""
    num = templates.env.filters["num"]
    op = e["op"]
    if op in ("take", "put", "adjust"):
        d = e["delta"] or 0  # a clamped take logs delta 0, so the verb comes from the op
        verb = "took" if op == "take" else ("added" if op == "put" else ("took" if d < 0 else "added"))
        return f"{verb} {num(abs(d))} → {num(e['qty_after'])}"
    if op == "set":
        return f"set to {num(e['qty_after'])}"
    if op == "on_the_way":
        return "marked on the way" if (e["note"] or "").endswith("True") else "cleared on the way"
    if op == "create":
        return f"created with {num(e['qty_after'])}"
    if op == "delete":
        return f"deleted (had {num(e['qty_before'])})"
    if op == "edit":
        return (e["note"] or "edited").replace("fields: ", "edited ")
    return e["note"] or op  # alias_add / alias_rm notes are already readable


def _day_label(iso_day: str) -> str:
    day = datetime.strptime(iso_day, "%Y-%m-%d").date()
    delta = (datetime.now().date() - day).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return day.strftime("%a %d %b").replace(" 0", " ")


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, conn=Depends(get_conn)):
    limit = 200
    events = queries.recent_events(conn, limit=limit)
    days: list[tuple[str, list[dict]]] = []  # events arrive newest-first, so days are contiguous
    for e in events:
        e["line"] = _event_line(e)
        label = _day_label(e["local_time"][:10])
        if not days or days[-1][0] != label:
            days.append((label, []))
        days[-1][1].append(e)
    return templates.TemplateResponse(request, "history.html", {
        "request": request, "days": days,
        "truncated": len(events) == limit, "limit": limit,
    })


# --- admin ---
@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, conn=Depends(get_conn)):
    items = queries.list_items(conn, "all")
    return templates.TemplateResponse(request, "admin.html", {
        "request": request, "items": items,
        "categories": queries.categories(conn), "units": queries.units(conn),
    })


@app.get("/partials/admin-row/{item_id}", response_class=HTMLResponse)
def admin_row(request: Request, item_id: int, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "partials/admin_row.html", {"request": request, "i": _get_or_404(conn, item_id)})


@app.get("/partials/admin-row/{item_id}/edit", response_class=HTMLResponse)
def admin_row_edit(request: Request, item_id: int, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "partials/admin_edit_row.html", {
        "request": request, "i": _get_or_404(conn, item_id),
        "categories": queries.categories(conn), "units": queries.units(conn),
    })


@app.post("/items/{item_id}/edit", response_class=HTMLResponse)
def admin_save(request: Request, item_id: int, conn=Depends(get_conn),
               item: str = Form(...), category: str = Form(...), unit: str = Form(...),
               quantity: float | None = Form(None), step: float | None = Form(None),
               low_stock_threshold: float | None = Form(None),
               aliases: str | None = Form(None), shopping_item_name: str | None = Form(None),
               notes: str | None = Form(None),
               necessity: int = Form(0)):  # checkbox: absent means unchecked
    _get_or_404(conn, item_id)
    # None = field absent from the form -> leave unchanged (update_item skips Nones),
    # so a partial form can't silently wipe fields it doesn't carry.
    fields = {
        "item": item, "category": category, "unit": unit, "quantity": quantity, "step": step,
        "low_stock_threshold": low_stock_threshold, "aliases": aliases,
        "shopping_item_name": shopping_item_name, "notes": notes, "necessity": necessity,
    }
    try:
        with db.transaction(conn):
            mutations.update_item(conn, item_id, fields, source="web")
    except mutations.ValidationError as e:
        return PlainTextResponse(str(e), status_code=422)
    return templates.TemplateResponse(request, "partials/admin_row.html", {"request": request, "i": queries.get_item(conn, item_id)})


@app.post("/items", response_class=HTMLResponse)
def admin_create(request: Request, conn=Depends(get_conn),
                 item: str = Form(...), category: str = Form(...), unit: str = Form("units"),
                 quantity: float = Form(0), low_stock_threshold: float = Form(0),
                 necessity: int = Form(0)):
    try:
        with db.transaction(conn):
            created = mutations.create_item(conn, {
                "item": item, "category": category, "unit": unit, "quantity": quantity,
                "low_stock_threshold": low_stock_threshold, "necessity": necessity,
            }, source="web")
    except mutations.ValidationError as e:
        return PlainTextResponse(str(e), status_code=422)
    return templates.TemplateResponse(request, "partials/admin_row.html", {"request": request, "i": queries.get_item(conn, created["id"])})


@app.post("/items/{item_id}/delete", response_class=HTMLResponse)
def admin_delete(item_id: int, conn=Depends(get_conn)):
    _get_or_404(conn, item_id)
    with db.transaction(conn):
        mutations.delete_item(conn, item_id, source="web")
    return HTMLResponse("")


# --- import / export ---
@app.get("/import-export", response_class=HTMLResponse)
def import_export(request: Request, conn=Depends(get_conn)):
    count = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    return templates.TemplateResponse(request, "import_export.html", {"request": request, "count": count})


@app.get("/export/csv")
def export_csv(conn=Depends(get_conn)):
    return Response(
        exporters.export_csv_string(conn), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=inventory.csv"},
    )


def _import_result(ok: bool, message: str):
    tone = "border-basil/40 bg-basil-tint text-basil" if ok else "border-amber/40 bg-amber-tint text-amber"
    return HTMLResponse(
        f'<div class="rounded-xl border {tone} px-3 py-2 text-sm font-medium">{html.escape(message)}</div>'
    )


@app.post("/import/csv", response_class=HTMLResponse)
def import_csv(file: UploadFile, conn=Depends(get_conn)):  # sync like every route: sqlite conns are thread-bound
    try:
        text = file.file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return _import_result(False, "That file isn't UTF-8 text — export a CSV first.")
    # snapshot next to the live DB before any bulk write (stated backup policy)
    db_file = conn.execute("PRAGMA database_list").fetchone()["file"]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")  # microseconds: same-second imports must not share a snapshot
    exporters.backup_to(Path(db_file).parent / "backups" / f"inventory-{ts}-pre-import.db", conn)
    try:
        with db.transaction(conn):
            r = exporters.import_csv_string(conn, text, source="csv-import")
    except mutations.ValidationError as e:
        return _import_result(False, f"Import aborted, nothing changed: {e}")
    return _import_result(
        True, f"Imported: {r['created']} new, {r['updated']} updated, {r['unchanged']} unchanged.")


@app.get("/backup/sqlite")
def backup_sqlite(conn=Depends(get_conn)):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    exporters.backup_to(tmp.name, conn)
    return FileResponse(tmp.name, media_type="application/octet-stream", filename="inventory-backup.db",
                        background=BackgroundTask(os.unlink, tmp.name))
