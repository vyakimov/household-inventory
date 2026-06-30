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
    code, env = run(db_path, "put", "TP", "1")
    assert code == 2 and env["error"]["type"] == "resource_not_found"


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
    run(db_path, "put", "--id", str(tp_id), "1", "--learn-alias", "TP")
    _, env = run(db_path, "get", "TP")
    assert env["ok"] and env["result"]["item"] == "Toilet paper"


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


def test_list_actions(db_path):
    code, env = run(db_path, "list-actions")
    names = [a["name"] for a in env["result"]["actions"]]
    assert "take" in names and "catalog" in names and "batch" in names
