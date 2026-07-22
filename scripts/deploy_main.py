"""Deploy an exact, tested main-branch commit with health-check rollback."""
import argparse
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class DeploymentError(RuntimeError):
    """A deployment safety check or operation failed."""


class ReleaseFailed(DeploymentError):
    """A release failed its deployment checks but rollback succeeded."""


@dataclass(frozen=True)
class Config:
    repo: Path
    sha: str
    label: str = "com.vy.inventory"
    health_url: str = "http://127.0.0.1:8502/"
    health_attempts: int = 20
    health_interval: float = 1.0

    @property
    def marker(self) -> Path:
        return self.repo.parent / ".inventory-deploy"

    @property
    def source_plist(self) -> Path:
        return self.repo / "deploy" / f"{self.label}.plist"

    @property
    def installed_plist(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self.label}.plist"


def _command(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(args), flush=True)
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise DeploymentError(f"{' '.join(args)}: {message}")
    return result


class Runtime:
    """Production service operations, separated so deployment behavior is testable."""

    def __init__(self, config: Config):
        self.config = config
        self.domain = f"gui/{os.getuid()}"

    def stop(self) -> None:
        service = f"{self.domain}/{self.config.label}"
        if _command(["launchctl", "print", service], cwd=self.config.repo,
                    check=False).returncode:
            return
        _command(
            ["launchctl", "bootout", service],
            cwd=self.config.repo,
        )

    def sync(self) -> None:
        uv = shutil.which("uv")
        if uv is None:
            raise DeploymentError("uv is not available on PATH")
        _command([uv, "sync", "--frozen"], cwd=self.config.repo)

    def start(self) -> None:
        _command(
            ["launchctl", "bootstrap", self.domain, str(self.config.installed_plist)],
            cwd=self.config.repo,
        )

    def healthy(self) -> bool:
        for _ in range(self.config.health_attempts):
            try:
                with urllib.request.urlopen(self.config.health_url, timeout=2) as response:
                    if response.status == 200:
                        return True
            except OSError:
                pass
            time.sleep(self.config.health_interval)
        return False


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _command(["git", *args], cwd=repo, check=check)


def _git_output(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _validate(config: Config) -> tuple[str, str]:
    if not config.marker.is_file():
        raise DeploymentError(f"refusing unmarked deployment checkout: {config.repo}")
    if not (config.repo / ".git").exists():
        raise DeploymentError(f"deployment checkout is not a git repository: {config.repo}")
    if _git_output(config.repo, "status", "--porcelain"):
        raise DeploymentError("deployment checkout has local changes")

    old_sha = _git_output(config.repo, "rev-parse", "HEAD")
    _git(config.repo, "fetch", "--quiet", "origin", "main")
    target_sha = _git_output(config.repo, "rev-parse", f"{config.sha}^{{commit}}")
    remote_main = _git_output(config.repo, "rev-parse", "origin/main")
    if _git(config.repo, "merge-base", "--is-ancestor", target_sha, remote_main,
            check=False).returncode:
        raise DeploymentError(f"target {target_sha} is not on origin/main")
    if _git(config.repo, "merge-base", "--is-ancestor", old_sha, target_sha,
            check=False).returncode:
        raise DeploymentError(f"deployment from {old_sha} to {target_sha} is not a fast-forward")
    return old_sha, target_sha


def _install_plist(config: Config) -> None:
    config.installed_plist.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config.source_plist, config.installed_plist)


def _restore_plist(config: Config, previous: bytes | None) -> None:
    if previous is None:
        config.installed_plist.unlink(missing_ok=True)
    else:
        config.installed_plist.write_bytes(previous)


def deploy(config: Config, runtime: Runtime | None = None) -> tuple[str, str]:
    """Deploy config.sha and return (old_sha, deployed_sha), rolling back on failure."""
    old_sha, target_sha = _validate(config)
    runtime = runtime or Runtime(config)
    if old_sha == target_sha:
        if not runtime.healthy():
            raise DeploymentError(f"{target_sha} is deployed but unhealthy")
        print(f"already deployed and healthy: {target_sha}")
        return old_sha, target_sha

    previous_plist = (
        config.installed_plist.read_bytes() if config.installed_plist.exists() else None
    )
    try:
        _git(config.repo, "checkout", "--detach", "--force", target_sha)
        _install_plist(config)
        runtime.stop()
        runtime.sync()
        runtime.start()
        if not runtime.healthy():
            raise DeploymentError(f"health check failed for {target_sha}")
    except Exception as deploy_error:
        print(f"deployment failed, rolling back: {deploy_error}", flush=True)
        try:
            runtime.stop()
            _git(config.repo, "checkout", "--detach", "--force", old_sha)
            _restore_plist(config, previous_plist)
            runtime.sync()
            runtime.start()
            if not runtime.healthy():
                raise DeploymentError(f"rollback to {old_sha} is unhealthy")
        except Exception as rollback_error:
            raise DeploymentError(
                f"deployment failed ({deploy_error}); rollback also failed ({rollback_error})"
            ) from rollback_error
        raise ReleaseFailed(f"deployment failed and rolled back: {deploy_error}") from deploy_error

    print(f"deployed {target_sha} (previously {old_sha})")
    return old_sha, target_sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--health-url", default="http://127.0.0.1:8502/")
    args = parser.parse_args()
    try:
        deploy(Config(repo=args.repo.resolve(), sha=args.sha, health_url=args.health_url))
    except DeploymentError as error:
        print(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
