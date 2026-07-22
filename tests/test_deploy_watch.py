import json

import pytest

from scripts import deploy_main, deploy_watch


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_latest_successful_sha_reads_main_push(monkeypatch):
    payload = {
        "workflow_runs": [{
            "head_sha": "abc123", "head_branch": "main", "conclusion": "success"
        }]
    }
    monkeypatch.setattr(deploy_watch.urllib.request, "urlopen", lambda request, timeout: Response(payload))

    assert deploy_watch.latest_successful_sha() == "abc123"


def test_deploy_latest_skips_sha_that_already_failed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "failed-sha").write_text("bad123\n")
    monkeypatch.setattr(deploy_watch, "latest_successful_sha", lambda: "bad123")
    monkeypatch.setattr(
        deploy_main, "deploy", lambda config: pytest.fail("failed SHA was retried")
    )

    assert deploy_watch.deploy_latest(repo) is False


def test_deploy_latest_records_failed_sha(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(deploy_watch, "latest_successful_sha", lambda: "bad123")
    monkeypatch.setattr(deploy_main, "_git_output", lambda *args: "old123")
    monkeypatch.setattr(
        deploy_main,
        "deploy",
        lambda config: (_ for _ in ()).throw(deploy_main.ReleaseFailed("unhealthy")),
    )

    with pytest.raises(deploy_main.DeploymentError, match="unhealthy"):
        deploy_watch.deploy_latest(repo)

    assert (tmp_path / "failed-sha").read_text() == "bad123\n"
