"""FastAPI app: phone-first inventory UI with HTMX partials. No auth (LAN-only, v1)."""
import tempfile

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, exporters, mutations, queries, settings

app = FastAPI(title="Household Inventory")
app.mount("/static", StaticFiles(directory=settings.BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(settings.BASE_DIR / "app" / "templates"))
templates.env.filters["num"] = lambda v: ("%g" % float(v)) if v is not None else ""

TABS = ["low", "necessities", "all"]


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _inventory_ctx(request, conn, tab, q):
    tab = tab if tab in TABS else "low"
    items = queries.list_items(conn, tab, q)
    return {
        "request": request,
        "tab": tab,
        "q": q or "",
        "groups": queries.group_by_category(items),
        "low_count": queries.count_low(conn),
        "categories": queries.categories(conn),
        "units": queries.units(conn),
    }


def _card(request, conn, item_id):
    item = queries.get_item(conn, item_id)
    return templates.TemplateResponse(request, "partials/item_card.html", {"i": item})


# --- pages ---
@app.get("/", response_class=HTMLResponse)
def index(request: Request, tab: str = "low", q: str | None = None, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "inventory.html", _inventory_ctx(request, conn, tab, q))


@app.get("/partials/inventory", response_class=HTMLResponse)
def partial_inventory(request: Request, tab: str = "low", q: str | None = None, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "partials/inventory_body.html", _inventory_ctx(request, conn, tab, q))


@app.get("/partials/list", response_class=HTMLResponse)
def partial_list(request: Request, tab: str = "all", q: str | None = None, conn=Depends(get_conn)):
    items = queries.list_items(conn, tab if tab in TABS else "all", q)
    return templates.TemplateResponse(request, "partials/item_list.html",
                                      {"request": request, "groups": queries.group_by_category(items)})


@app.get("/partials/item/{item_id}", response_class=HTMLResponse)
def partial_item(request: Request, item_id: int, conn=Depends(get_conn)):
    return _card(request, conn, item_id)


# --- card mutations (return the refreshed card) ---
@app.post("/items/{item_id}/inc", response_class=HTMLResponse)
def item_inc(request: Request, item_id: int, conn=Depends(get_conn)):
    item = queries.get_item(conn, item_id)
    with db.transaction(conn):
        mutations.adjust_quantity(conn, item_id, item["step"], op="put", source="web")
    return _card(request, conn, item_id)


@app.post("/items/{item_id}/dec", response_class=HTMLResponse)
def item_dec(request: Request, item_id: int, conn=Depends(get_conn)):
    item = queries.get_item(conn, item_id)
    with db.transaction(conn):
        mutations.adjust_quantity(conn, item_id, -item["step"], op="take", source="web")
    return _card(request, conn, item_id)


@app.post("/items/{item_id}/quantity", response_class=HTMLResponse)
def item_set_quantity(request: Request, item_id: int, quantity: float = Form(...), conn=Depends(get_conn)):
    with db.transaction(conn):
        mutations.set_quantity(conn, item_id, max(0.0, quantity), source="web")
    return _card(request, conn, item_id)


@app.post("/items/{item_id}/on-the-way", response_class=HTMLResponse)
def item_on_the_way(request: Request, item_id: int, value: int = Form(...), conn=Depends(get_conn)):
    with db.transaction(conn):
        mutations.set_on_the_way(conn, item_id, bool(value), source="web")
    return _card(request, conn, item_id)


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
    return templates.TemplateResponse(request, "partials/admin_row.html", {"request": request, "i": queries.get_item(conn, item_id)})


@app.get("/partials/admin-row/{item_id}/edit", response_class=HTMLResponse)
def admin_row_edit(request: Request, item_id: int, conn=Depends(get_conn)):
    return templates.TemplateResponse(request, "partials/admin_edit_row.html", {
        "request": request, "i": queries.get_item(conn, item_id),
        "categories": queries.categories(conn), "units": queries.units(conn),
    })


@app.post("/items/{item_id}/edit", response_class=HTMLResponse)
def admin_save(request: Request, item_id: int, conn=Depends(get_conn),
               item: str = Form(...), category: str = Form(...), unit: str = Form(...),
               quantity: float = Form(0), step: float = Form(1), low_stock_threshold: float = Form(0),
               aliases: str = Form(""), shopping_item_name: str = Form(""), notes: str = Form(""),
               necessity: int = Form(0)):
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


@app.get("/backup/sqlite")
def backup_sqlite(conn=Depends(get_conn)):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    exporters.backup_to(tmp.name, conn)
    return FileResponse(tmp.name, media_type="application/octet-stream", filename="inventory-backup.db")
