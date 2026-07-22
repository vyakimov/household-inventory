from app import queries


def test_search_treats_punctuation_as_literal_input(conn):
    # SQL wildcard characters must not turn into match-all searches.
    assert queries.list_items(conn, "all", "%") == []
    assert queries.list_items(conn, "all", "_") == []
    assert len(queries.list_items(conn, "all", "cat")) == 2


def test_search_matches_category_but_ranks_item_hit_first(conn):
    conn.execute("INSERT INTO categories(name, sort_order) VALUES ('snacks and breakfast', 20)")
    conn.execute(
        "INSERT INTO items(item, category, unit) VALUES "
        "('Breakfast cereal', 'snacks and breakfast', 'units'), "
        "('Fruit bar', 'snacks and breakfast', 'units')"
    )

    results = queries.list_items(conn, "all", "breakfast")

    assert [row["item"] for row in results] == ["Breakfast cereal", "Fruit bar"]


def test_search_is_fuzzy_and_uses_conservative_stemming(conn):
    assert [row["item"] for row in queries.list_items(conn, "all", "granla")] == ["Granola"]
    assert [row["item"] for row in queries.list_items(conn, "all", "granolas")] == ["Granola"]


def test_suggest_matches_aliases(conn):
    # "bog rill" is only close to the alias "bog roll", never the canonical name
    out = queries.suggest(conn, "bog rill")
    assert {"id": 3, "item": "Toilet paper"} in out


def test_suggest_dedupes_by_item(conn):
    ids = [s["id"] for s in queries.suggest(conn, "cat food")]
    assert len(ids) == len(set(ids))
