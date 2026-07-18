from app import queries


def test_search_escapes_like_wildcards(conn):
    # '%' and '_' are literal characters in a search, not wildcards
    assert queries.list_items(conn, "all", "%") == []
    assert queries.list_items(conn, "all", "_") == []
    assert len(queries.list_items(conn, "all", "cat")) == 2


def test_suggest_matches_aliases(conn):
    # "bog rill" is only close to the alias "bog roll", never the canonical name
    out = queries.suggest(conn, "bog rill")
    assert {"id": 3, "item": "Toilet paper"} in out


def test_suggest_dedupes_by_item(conn):
    ids = [s["id"] for s in queries.suggest(conn, "cat food")]
    assert len(ids) == len(set(ids))
