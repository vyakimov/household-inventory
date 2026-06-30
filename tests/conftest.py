import pytest

from app import db

CATEGORIES = ["food", "cleaning", "cats", "paper goods"]
UNITS = ["units", "bags", "cans", "rolls"]

# item, category, qty, unit, threshold, necessity, on_the_way, aliases
FIXTURE_ITEMS = [
    ("Dry cat food", "cats", 0.1, "bags", 0.2, 1, 0, "kibble"),
    ("Wet cat food", "cats", 9, "cans", 12, 1, 1, ""),          # low but on the way
    ("Toilet paper", "paper goods", 4, "rolls", 2, 1, 0, "loo roll; bog roll"),  # not low
    ("Black beans", "food", 2, "cans", 0, 0, 0, ""),            # not a necessity
    ("Granola", "food", 1, "units", 1, 1, 0, ""),               # low (inclusive)
    ("Salt", "food", 0, "units", 0, 1, 0, ""),                  # necessity but threshold 0
]


def _seed(conn):
    for i, name in enumerate(CATEGORIES):
        conn.execute("INSERT INTO categories(name, sort_order) VALUES (?, ?)", (name, i))
    for u in UNITS:
        conn.execute("INSERT INTO units(name) VALUES (?)", (u,))
    for row in FIXTURE_ITEMS:
        conn.execute(
            "INSERT INTO items(item, category, quantity, unit, low_stock_threshold,"
            " necessity, on_the_way, aliases) VALUES (?,?,?,?,?,?,?,?)", row)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    conn = db.connect(path)
    db.init_db(conn)
    _seed(conn)
    conn.close()
    return path


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()


@pytest.fixture
def client(db_path):
    from fastapi.testclient import TestClient

    from app.main import app, get_conn

    def _conn():
        c = db.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_conn] = _conn
    yield TestClient(app)
    app.dependency_overrides.clear()
