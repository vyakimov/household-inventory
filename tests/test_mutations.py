import pytest

from app import db, mutations, queries


def _id(conn, name):
    return conn.execute("SELECT id FROM items WHERE item = ?", (name,)).fetchone()["id"]


def test_take_clamps_at_zero(conn):
    i = _id(conn, "Granola")
    with db.transaction(conn):
        r = mutations.adjust_quantity(conn, i, -5, op="take")
    assert r["after"] == 0 and r["clamped"] is True


def test_put_increases(conn):
    i = _id(conn, "Granola")
    with db.transaction(conn):
        r = mutations.adjust_quantity(conn, i, 2, op="put")
    assert r["before"] == 1 and r["after"] == 3 and r["clamped"] is False


def test_set_negative_raises(conn):
    with pytest.raises(mutations.ValidationError):
        mutations.set_quantity(conn, _id(conn, "Granola"), -1)


def test_low_changed_flag(conn):
    # Granola is low at qty 1/threshold 1; bumping to 5 clears low
    i = _id(conn, "Granola")
    with db.transaction(conn):
        r = mutations.set_quantity(conn, i, 5)
    assert r["is_low"] is False and r["low_changed"] is True


def test_update_only_intended_fields(conn):
    i = _id(conn, "Granola")
    before = queries.get_item(conn, i)
    with db.transaction(conn):
        mutations.update_item(conn, i, {"category": "cleaning"})
    after = queries.get_item(conn, i)
    assert after["category"] == "cleaning"
    assert after["quantity"] == before["quantity"] and after["unit"] == before["unit"]


def test_update_bad_category_raises(conn):
    with pytest.raises(mutations.ValidationError):
        mutations.update_item(conn, _id(conn, "Granola"), {"category": "nope"})


@pytest.mark.parametrize("threshold", [-0.1, -2])
def test_update_rejects_ambiguous_negative_threshold(conn, threshold):
    with pytest.raises(mutations.ValidationError, match="must be -1 or >= 0"):
        mutations.update_item(
            conn, _id(conn, "Granola"), {"low_stock_threshold": threshold}
        )


def test_update_accepts_disabled_threshold(conn):
    with db.transaction(conn):
        item = mutations.update_item(
            conn, _id(conn, "Granola"), {"low_stock_threshold": -1}
        )
    assert item["low_stock_threshold"] == -1 and item["is_low"] == 0


def test_add_alias_dedupe_case_insensitive(conn):
    i = _id(conn, "Wet cat food")
    with db.transaction(conn):
        mutations.add_alias(conn, i, "WCF")
        mutations.add_alias(conn, i, "wcf")
    aliases = queries.split_aliases(queries.get_item(conn, i)["aliases"])
    assert aliases == ["WCF"]


def test_add_alias_rejects_separators(conn):
    with pytest.raises(mutations.ValidationError):
        mutations.add_alias(conn, _id(conn, "Granola"), "muesli, kibble")


def test_alias_split_handles_semicolon_and_comma(conn):
    # Toilet paper fixture uses "loo roll; bog roll"
    aliases = queries.split_aliases(queries.get_item(conn, _id(conn, "Toilet paper"))["aliases"])
    assert aliases == ["loo roll", "bog roll"]


def test_event_logged(conn):
    i = _id(conn, "Granola")
    with db.transaction(conn):
        mutations.adjust_quantity(conn, i, 1, op="put", source="test")
    e = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 1").fetchone()
    assert e["op"] == "put" and e["source"] == "test" and e["item_name"] == "Granola"
