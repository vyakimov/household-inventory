import contextlib
import io
import json
import sys

from app import cli, db, embeddings


def run(db_path, *args, stdin=None):
    out = io.StringIO()
    old_stdin = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out):
            code = cli.main(["--db", db_path, *args])
    finally:
        sys.stdin = old_stdin
    return code, json.loads(out.getvalue())


def test_resolve_by_alias(db_path):
    code, env = run(db_path, "get", "kibble")
    assert code == 0 and env["ok"] and env["result"]["item"] == "Dry cat food"


def test_ambiguous_refused(db_path):
    code, env = run(db_path, "take", "cat food", "1")
    assert code == 3 and env["error"]["type"] == "ambiguous_match"
    assert len(env["error"]["details"]["candidates"]) == 2


def test_not_found(db_path):
    code, env = run(db_path, "put", "toilet rolls", "1")
    assert code == 2 and env["error"]["type"] == "resource_not_found"
    assert {"id": 3, "item": "Toilet paper"} in env["error"]["details"]["suggestions"]


def test_search_merges_like_and_semantic_results(db_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    conn = db.connect(db_path)
    conn.execute(
        "INSERT INTO items(item, category, unit) VALUES ('Bathroom bleach', 'cleaning', 'units')"
    )
    conn.close()

    def fake(texts):
        return [
            [1.0, 0.0]
            if any(word in text.lower() for word in ("bathroom", "bleach", "cleaner", "cleaning"))
            else [0.0, 1.0]
            for text in texts
        ]

    monkeypatch.setattr(embeddings, "_request_embeddings", fake)
    code, env = run(db_path, "search", "Granola", "--limit", "8")
    assert code == 0 and env["result"]["items"][0]["source"] == "like"
    code, env = run(db_path, "search", "--query", "bathroom cleaner")
    assert code == 0 and env["result"]["items"][0] == {
        "id": 7, "item": "Bathroom bleach", "category": "cleaning", "quantity": 0.0,
        "unit": "units", "source": "semantic", "score": 1.0,
    }


def test_search_works_with_semantic_disabled(db_path):
    code, env = run(db_path, "search", "Granola")
    assert code == 0 and env["result"]["items"] == [{
        "id": 5, "item": "Granola", "category": "food", "quantity": 1.0,
        "unit": "units", "source": "like", "score": None,
    }]


def test_not_found_suggestions_include_semantic_candidate(db_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    conn = db.connect(db_path)
    conn.execute(
        "INSERT INTO items(item, category, unit) VALUES ('Bathroom bleach', 'cleaning', 'units')"
    )
    conn.close()
    monkeypatch.setattr(
        embeddings,
        "_request_embeddings",
        lambda texts: [
            [1.0, 0.0]
            if any(word in text.lower() for word in ("bathroom", "bleach", "cleaner", "cleaning"))
            else [0.0, 1.0]
            for text in texts
        ],
    )
    code, env = run(db_path, "get", "bathroom cleaning product")
    assert code == 2
    assert {"id": 7, "item": "Bathroom bleach"} in env["error"]["details"]["suggestions"]


def test_take_clamped(db_path):
    code, env = run(db_path, "take", "Granola", "5")
    assert env["result"]["after"] == 0 and env["result"]["clamped"] is True


def test_dry_run_does_not_persist(db_path):
    run(db_path, "take", "Granola", "1", "--dry-run")
    _, env = run(db_path, "get", "Granola")
    assert env["result"]["quantity"] == 1


def test_idempotent_request_id(db_path):
    run(db_path, "put", "Granola", "1", "--request-id", "x1")
    _, env = run(db_path, "put", "Granola", "1", "--request-id", "x1")
    assert env["meta"].get("idempotent_replay") is True
    _, env = run(db_path, "get", "Granola")
    assert env["result"]["quantity"] == 2  # applied once only (1 -> 2)


def test_learn_alias_then_resolve(db_path):
    _, env = run(db_path, "catalog")
    tp_id = next(i["id"] for i in env["result"]["items"] if i["item"] == "Toilet paper")
    code, env = run(db_path, "put", "--id", str(tp_id), "1", "--learn-alias", "TP")
    assert code == 0 and env["ok"] and env["result"]["learned_alias"] == "TP"
    _, env = run(db_path, "get", "TP")
    assert env["ok"] and env["result"]["item"] == "Toilet paper"


def test_item_option_and_qty_option(db_path):
    code, env = run(db_path, "put", "--item", "Granola", "--qty", "2")
    assert code == 0 and env["ok"] and env["result"]["after"] == 3


def test_item_option_with_positional_qty(db_path):
    code, env = run(db_path, "take", "--item", "Granola", "1")
    assert code == 0 and env["ok"] and env["result"]["after"] == 0


def test_on_the_way_item_option(db_path):
    code, env = run(db_path, "on-the-way", "--item", "Granola", "true")
    assert code == 0 and env["ok"] and env["result"]["on_the_way"] is True


def test_on_the_way_id_with_positional_value(db_path):
    _, env = run(db_path, "catalog")
    item_id = next(i["id"] for i in env["result"]["items"] if i["item"] == "Granola")
    code, env = run(db_path, "on-the-way", "--id", str(item_id), "true")
    assert code == 0 and env["ok"] and env["result"]["on_the_way"] is True


def test_alias_item_option(db_path):
    code, env = run(db_path, "alias", "add", "--item", "Toilet paper", "--value", "TP")
    assert code == 0 and env["ok"] and "TP" in env["result"]["aliases"]


def test_alias_id_with_positional_value(db_path):
    _, env = run(db_path, "catalog")
    item_id = next(i["id"] for i in env["result"]["items"] if i["item"] == "Toilet paper")
    code, env = run(db_path, "alias", "add", "--id", str(item_id), "TP")
    assert code == 0 and env["ok"] and "TP" in env["result"]["aliases"]


def test_alias_collision_is_rejected(db_path):
    code, env = run(db_path, "alias", "add", "Wet cat food", "kibble")
    assert code == 4 and env["error"]["type"] == "invalid_arguments"


def test_needs_buy_tab(db_path):
    code, env = run(db_path, "list", "--tab", "needs-buy")
    assert code == 0 and env["ok"]
    assert all(item["needs_buy"] for item in env["result"]["items"])


def test_lookups(db_path):
    code, env = run(db_path, "lookups")
    assert code == 0 and env["ok"]
    assert "food" in env["result"]["categories"]
    assert "rolls" in env["result"]["units"]


def test_category_add_and_dry_run(db_path):
    code, env = run(db_path, "category", "add", "spices", "--dry-run")
    assert code == 0 and env["ok"] and env["result"]["created"] is True
    _, env = run(db_path, "lookups")
    assert "spices" not in env["result"]["categories"]

    code, env = run(db_path, "category", "add", "spices")
    assert code == 0 and env["ok"] and env["result"]["created"] is True
    _, env = run(db_path, "lookups")
    assert "spices" in env["result"]["categories"]


def test_category_add_can_reorder_existing_category(db_path):
    code, env = run(db_path, "category", "add", "food", "--sort-order", "99")
    assert code == 0 and env["result"]["created"] is False
    assert env["result"]["updated"] is True
    _, env = run(db_path, "category", "list")
    food = next(row for row in env["result"]["categories"] if row["name"] == "food")
    assert food["sort_order"] == 99


def test_category_rm_in_use_is_conflict(db_path):
    code, env = run(db_path, "category", "rm", "food")
    assert code == 5 and env["error"]["type"] == "conflict"


def test_batch_atomic_rollback(db_path):
    ops = '[{"op":"take","item":"Granola","qty":1},{"op":"put","item":"cat food","qty":1}]'
    code, env = run(db_path, "batch", stdin=ops)
    assert code == 3 and env["error"]["type"] == "ambiguous_match"
    _, env = run(db_path, "get", "Granola")
    assert env["result"]["quantity"] == 1  # first op rolled back


def test_batch_success(db_path):
    ops = '[{"op":"take","item":"Granola","qty":1},{"op":"put","item":"Black beans","qty":3}]'
    code, env = run(db_path, "batch", stdin=ops)
    assert code == 0 and env["ok"] and env["result"]["count"] == 2


def test_batch_set_requires_qty(db_path):
    code, env = run(db_path, "batch", stdin='[{"op":"set","item":"Granola"}]')
    assert code == 4 and env["error"]["type"] == "invalid_arguments"
    _, env = run(db_path, "get", "Granola")
    assert env["result"]["quantity"] == 1  # not silently zeroed


def test_batch_bad_qty_is_invalid_arguments(db_path):
    code, env = run(db_path, "batch", stdin='[{"op":"put","item":"Granola","qty":"lots"}]')
    assert code == 4 and env["error"]["type"] == "invalid_arguments"


def test_batch_on_the_way_requires_value(db_path):
    code, env = run(db_path, "batch", stdin='[{"op":"on_the_way","item":"Wet cat food"}]')
    assert code == 4 and env["error"]["type"] == "invalid_arguments"
    _, env = run(db_path, "get", "Wet cat food")
    assert env["result"]["on_the_way"] == 1  # flag untouched


def test_batch_categorize_and_dry_run(db_path):
    ops = '[{"op":"categorize","item":"Granola","category":"cleaning"}]'
    code, env = run(db_path, "batch", "--dry-run", stdin=ops)
    assert code == 0 and env["ok"]
    _, env = run(db_path, "get", "Granola")
    assert env["result"]["category"] == "food"

    code, env = run(db_path, "batch", stdin=ops)
    assert code == 0 and env["ok"]
    _, env = run(db_path, "get", "Granola")
    assert env["result"]["category"] == "cleaning"


def test_batch_alias_add_and_dry_run(db_path):
    ops = '[{"op":"alias_add","item":"Granola","alias":"breakfast cereal"}]'
    code, env = run(db_path, "batch", "--dry-run", stdin=ops)
    assert code == 0 and env["ok"]
    _, env = run(db_path, "get", "Granola")
    assert "breakfast cereal" not in env["result"]["aliases"]

    code, env = run(db_path, "batch", stdin=ops)
    assert code == 0 and env["ok"]
    _, env = run(db_path, "get", "breakfast cereal")
    assert env["result"]["item"] == "Granola"


def test_rename_conflict(db_path):
    code, env = run(db_path, "edit", "Granola", "--rename", "Salt")
    assert code == 5 and env["error"]["type"] == "conflict"


def test_unopenable_db_still_emits_envelope(tmp_path):
    code, env = run(str(tmp_path / "no-such-dir" / "x.db"), "get", "Granola")
    assert code == 1 and env["ok"] is False
    assert env["error"]["type"] == "internal_error"


def test_list_actions(db_path):
    code, env = run(db_path, "list-actions")
    names = [a["name"] for a in env["result"]["actions"]]
    assert "take" in names and "catalog" in names and "batch" in names and "lookups" in names
    assert "category" in names
    assert "search" in names
