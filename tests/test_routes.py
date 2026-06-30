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
