import contextlib
import io
import json
import sys

from app import cli


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


def test_needs_buy_tab(db_path):
    code, env = run(db_path, "list", "--tab", "needs-buy")
    assert code == 0 and env["ok"]
    assert all(item["needs_buy"] for item in env["result"]["items"])


def test_lookups(db_path):
    code, env = run(db_path, "lookups")
    assert code == 0 and env["ok"]
    assert "food" in env["result"]["categories"]
    assert "rolls" in env["result"]["units"]


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
