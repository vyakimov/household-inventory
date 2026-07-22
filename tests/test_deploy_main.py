import subprocess

import pytest

from scripts import deploy_main


class FakeRuntime:
    def __init__(self, health_results=(True,)):
        self.calls = []
        self.health_results = iter(health_results)

    def stop(self):
        self.calls.append("stop")

    def sync(self):
        self.calls.append("sync")

    def start(self):
        self.calls.append("start")

    def healthy(self):
        self.calls.append("healthy")
        return next(self.health_results)


def _run(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


def _deployment_repo(tmp_path):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    deployed = tmp_path / "deploy" / "repo"
    _run(tmp_path, "init", "--bare", str(remote))
    _run(tmp_path, "clone", str(remote), str(seed))
    _run(seed, "config", "user.email", "tests@example.com")
    _run(seed, "config", "user.name", "Tests")
    (seed / "deploy").mkdir()
    plist = seed / "deploy" / "com.vy.inventory.plist"
    plist.write_text("old plist")
    (seed / "version").write_text("old")
    _run(seed, "add", ".")
    _run(seed, "commit", "-m", "old")
    _run(seed, "branch", "-M", "main")
    _run(seed, "push", "-u", "origin", "main")
    old_sha = _run(seed, "rev-parse", "HEAD")
    _run(tmp_path, "clone", "--branch", "main", str(remote), str(deployed))
    (deployed.parent / ".inventory-deploy").touch()
    plist.write_text("new plist")
    (seed / "version").write_text("new")
    _run(seed, "add", ".")
    _run(seed, "commit", "-m", "new")
    _run(seed, "push")
    new_sha = _run(seed, "rev-parse", "HEAD")
    return deployed, old_sha, new_sha


def test_deploys_exact_main_commit_after_health_check(tmp_path, monkeypatch):
    repo, old_sha, new_sha = _deployment_repo(tmp_path)
    installed = tmp_path / "installed.plist"
    installed.write_text("installed old plist")
    config = deploy_main.Config(repo=repo, sha=new_sha)
    monkeypatch.setattr(
        deploy_main.Config, "installed_plist", property(lambda self: installed)
    )
    runtime = FakeRuntime()

    assert deploy_main.deploy(config, runtime) == (old_sha, new_sha)
    assert _run(repo, "rev-parse", "HEAD") == new_sha
    assert installed.read_text() == "new plist"
    assert runtime.calls == ["stop", "sync", "start", "healthy"]


def test_refuses_dirty_deployment_checkout(tmp_path):
    repo, _, new_sha = _deployment_repo(tmp_path)
    (repo / "local-change").write_text("do not overwrite")

    with pytest.raises(deploy_main.DeploymentError, match="local changes"):
        deploy_main.deploy(deploy_main.Config(repo=repo, sha=new_sha), FakeRuntime())


def test_failed_health_check_restores_revision_plist_and_service(tmp_path, monkeypatch):
    repo, old_sha, new_sha = _deployment_repo(tmp_path)
    installed = tmp_path / "installed.plist"
    installed.write_text("installed old plist")
    config = deploy_main.Config(repo=repo, sha=new_sha)
    monkeypatch.setattr(
        deploy_main.Config, "installed_plist", property(lambda self: installed)
    )
    runtime = FakeRuntime((False, True))

    with pytest.raises(deploy_main.DeploymentError, match="failed and rolled back"):
        deploy_main.deploy(config, runtime)

    assert _run(repo, "rev-parse", "HEAD") == old_sha
    assert installed.read_text() == "installed old plist"
    assert runtime.calls == [
        "stop", "sync", "start", "healthy",
        "stop", "sync", "start", "healthy",
    ]
