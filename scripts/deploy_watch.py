"""Deploy the newest main commit whose GitHub Actions CI run succeeded."""
import fcntl
import json
import urllib.request
from pathlib import Path

from scripts import deploy_main

REPOSITORY = "vyakimov/household-inventory"
WORKFLOW = "ci-cd.yml"
API_URL = (
    f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs"
    "?branch=main&event=push&status=success&per_page=1"
)


def latest_successful_sha() -> str | None:
    request = urllib.request.Request(
        API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "inventory-deploy"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        runs = json.load(response).get("workflow_runs", [])
    if not runs:
        return None
    run = runs[0]
    if run.get("conclusion") != "success" or run.get("head_branch") != "main":
        return None
    return run.get("head_sha")


def deploy_latest(repo: Path) -> bool:
    """Deploy once when a new successful SHA exists; return whether deployment ran."""
    sha = latest_successful_sha()
    if not sha:
        print("no successful main CI run found")
        return False

    failed_sha = repo.parent / "failed-sha"
    if failed_sha.exists() and failed_sha.read_text().strip() == sha:
        print(f"not retrying previously failed deployment {sha}")
        return False

    current = deploy_main._git_output(repo, "rev-parse", "HEAD")
    if current == sha:
        print(f"already deployed: {sha}")
        return False

    try:
        deploy_main.deploy(deploy_main.Config(repo=repo, sha=sha))
    except deploy_main.ReleaseFailed:
        failed_sha.write_text(sha + "\n")
        raise
    failed_sha.unlink(missing_ok=True)
    return True


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    lock_path = repo.parent / "deploy.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            deploy_latest(repo)
    except BlockingIOError:
        print("another deployment check is already running")
        return 0
    except Exception as error:
        print(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
