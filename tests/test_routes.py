def _id(conn, name):
    return conn.execute("SELECT id FROM items WHERE item = ?", (name,)).fetchone()["id"]


def test_home_ok(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Inventory" in r.text and "Low stock" in r.text


def test_low_tab_filters(client):
    r = client.get("/partials/list", params={"tab": "low"})
    assert r.status_code == 200
    assert "Granola" in r.text and "Toilet paper" not in r.text


def test_search_matches_name(client):
    r = client.get("/partials/list", params={"tab": "all", "q": "cat"})
    assert "Dry cat food" in r.text and "Wet cat food" in r.text


def test_search_matches_alias(client):
    r = client.get("/partials/list", params={"tab": "all", "q": "kibble"})
    assert "Dry cat food" in r.text


def test_inc_persists(client, conn):
    i = _id(conn, "Granola")
    r = client.post(f"/items/{i}/inc")
    assert r.status_code == 200 and "Granola" in r.text
    qty = conn.execute("SELECT quantity FROM items WHERE id = ?", (i,)).fetchone()["quantity"]
    assert qty == 2


def test_on_the_way_toggle(client, conn):
    i = _id(conn, "Granola")
    r = client.post(f"/items/{i}/on-the-way", data={"value": 1})
    assert r.status_code == 200
    otw = conn.execute("SELECT on_the_way FROM items WHERE id = ?", (i,)).fetchone()["on_the_way"]
    assert otw == 1


def test_set_quantity(client, conn):
    i = _id(conn, "Granola")
    client.post(f"/items/{i}/quantity", data={"quantity": 4.5})
    qty = conn.execute("SELECT quantity FROM items WHERE id = ?", (i,)).fetchone()["quantity"]
    assert qty == 4.5


def test_admin_ok(client):
    r = client.get("/admin")
    assert r.status_code == 200 and "Add item" in r.text


def test_export_csv(client):
    r = client.get("/export/csv")
    assert r.status_code == 200 and r.text.startswith("item,aliases,category")


def test_bad_category_create_returns_422(client):
    r = client.post("/items", data={"item": "Test thing", "category": "bogus"})
    assert r.status_code == 422


def test_missing_item_returns_404(client):
    assert client.post("/items/9999/inc").status_code == 404
    assert client.get("/partials/item/9999").status_code == 404
    assert client.post("/items/9999/delete").status_code == 404


def test_admin_edit_keeps_notes(client, conn):
    i = _id(conn, "Granola")
    conn.execute("UPDATE items SET notes = 'buy the crunchy one' WHERE id = ?", (i,))
    # form without a notes field must not wipe notes
    r = client.post(f"/items/{i}/edit", data={"item": "Granola", "category": "food",
                                              "unit": "units", "quantity": 2})
    assert r.status_code == 200
    assert conn.execute("SELECT notes FROM items WHERE id = ?", (i,)).fetchone()["notes"] \
        == "buy the crunchy one"
    # and the edit form itself now round-trips notes
    r = client.get(f"/partials/admin-row/{i}/edit")
    assert 'name="notes"' in r.text and "buy the crunchy one" in r.text


def test_admin_rename_conflict_returns_422(client, conn):
    i = _id(conn, "Granola")
    r = client.post(f"/items/{i}/edit", data={"item": "Salt", "category": "food", "unit": "units"})
    assert r.status_code == 422 and "exists" in r.text


def test_low_change_fires_hx_trigger(client, conn):
    i = _id(conn, "Granola")  # qty 1, threshold 1 -> low
    r = client.post(f"/items/{i}/inc")  # 1 -> 2 clears low
    assert r.headers.get("HX-Trigger") == "low-changed"
    r = client.post(f"/items/{i}/inc")  # 2 -> 3, still not low
    assert "HX-Trigger" not in r.headers


def test_partial_inventory_updates_header_count_oob(client):
    r = client.get("/partials/inventory", params={"tab": "low"})
    assert 'hx-swap-oob' in r.text and "running low" in r.text
    # the full page renders the count exactly once (no stray oob span)
    assert client.get("/").text.count('id="low-count"') == 1


def test_import_csv_upserts(client, conn):
    csv_text = "item,category,quantity,unit\nGranola,food,9,units\nOat milk,food,2,cans\n"
    r = client.post("/import/csv", files={"file": ("inv.csv", csv_text, "text/csv")})
    assert r.status_code == 200 and "1 new, 1 updated" in r.text
    assert conn.execute("SELECT quantity FROM items WHERE item = 'Granola'").fetchone()[0] == 9
    assert conn.execute("SELECT 1 FROM items WHERE item = 'Oat milk'").fetchone()


def test_import_csv_bad_row_rolls_back_everything(client, conn):
    csv_text = "item,category,quantity\nGranola,food,9\nNew thing,bogus,1\n"
    r = client.post("/import/csv", files={"file": ("inv.csv", csv_text, "text/csv")})
    assert r.status_code == 200 and "nothing changed" in r.text and "line 3" in r.text
    assert conn.execute("SELECT quantity FROM items WHERE item = 'Granola'").fetchone()[0] == 1


def test_needs_buy_tab_filters(client):
    r = client.get("/partials/list", params={"tab": "needs-buy"})
    assert r.status_code == 200
    # low necessities not already on the way
    assert "Granola" in r.text and "Dry cat food" in r.text
    assert "Wet cat food" not in r.text  # low but already on the way


def test_home_has_buy_tab_with_count(client):
    text = client.get("/").text
    assert "To buy" in text and "needs-buy" in text


def test_history_page_shows_events(client, conn):
    i = _id(conn, "Granola")
    client.post(f"/items/{i}/inc")
    r = client.get("/history")
    assert r.status_code == 200
    assert "Granola" in r.text and "added 1" in r.text and "Today" in r.text


def test_history_page_empty_state(client):
    r = client.get("/history")
    assert r.status_code == 200 and "No changes recorded yet" in r.text


def test_import_csv_unknown_column_rejected(client, conn):
    r = client.post("/import/csv", files={"file": ("inv.csv", "item,quantiy\nGranola,9\n", "text/csv")})
    assert r.status_code == 200 and "unknown column" in r.text and "quantiy" in r.text
    assert conn.execute("SELECT quantity FROM items WHERE item = 'Granola'").fetchone()[0] == 1


def test_import_csv_takes_backup_first(client, conn, db_path):
    from pathlib import Path
    client.post("/import/csv", files={"file": ("inv.csv", "item,quantity\nGranola,3\n", "text/csv")})
    backups = list((Path(db_path).parent / "backups").glob("inventory-*-pre-import.db"))
    assert backups, "expected a pre-import backup snapshot"
