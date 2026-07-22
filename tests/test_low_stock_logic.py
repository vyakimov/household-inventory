from app import queries


def _flags(conn):
    return {r["item"]: r for r in conn.execute("SELECT item, is_low, needs_buy FROM v_items")}


def test_inclusive_threshold_is_low(conn):
    # Granola: qty 1, threshold 1 -> low (inclusive)
    assert _flags(conn)["Granola"]["is_low"] == 1


def test_on_the_way_excluded_from_needs_buy(conn):
    f = _flags(conn)["Wet cat food"]
    assert f["is_low"] == 1 and f["needs_buy"] == 0


def test_non_necessity_excluded(conn):
    assert _flags(conn)["Black beans"]["is_low"] == 0


def test_disabled_threshold_excluded(conn):
    assert _flags(conn)["Salt"]["is_low"] == 0


def test_zero_threshold_is_low_when_empty(conn):
    conn.execute("UPDATE items SET low_stock_threshold = 0 WHERE item = 'Salt'")
    f = _flags(conn)["Salt"]
    assert f["is_low"] == 1 and f["needs_buy"] == 1


def test_above_threshold_not_low(conn):
    assert _flags(conn)["Toilet paper"]["is_low"] == 0


def test_fractional_below_threshold_low(conn):
    f = _flags(conn)["Dry cat food"]  # 0.1 <= 0.2
    assert f["is_low"] == 1 and f["needs_buy"] == 1


def test_count_low(conn):
    # Dry cat food, Wet cat food, Granola
    assert queries.count_low(conn) == 3


def test_list_low_tab_excludes_non_low(conn):
    names = [i["item"] for i in queries.list_items(conn, "low")]
    assert "Granola" in names and "Toilet paper" not in names
