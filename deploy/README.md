# Deploying on macOS (launchd)

Runs Uvicorn bound to the LAN on port 8502, kept alive by launchd, plus a daily
backup and deployment jobs. No auth — reachable only on the LAN / over WireGuard
(v1). The service plists call binaries in the isolated deployment venv directly,
while the deploy watcher uses `uv` only while the web service is stopped.

## Continuous deployment

GitHub Actions runs Ruff and the full pytest suite on every pull request and every
push to `main`. A local LaunchAgent checks GitHub every five minutes and deploys only
the newest `main` commit whose `CI` workflow succeeded. The pull model is deliberate:
this repository is public, so the Mac is not exposed as a general-purpose GitHub
self-hosted runner.

Production code lives in the isolated `.deploy/repo` checkout. The database, `.env`,
backups, and logs remain in the project root. A deployment:

1. verifies the checkout is marked, clean, and can fast-forward to the tested SHA;
2. checks out that exact commit;
3. stops the web LaunchAgent and runs `uv sync --frozen`;
4. installs the versioned plist, starts the service, and retries an HTTP health check;
5. restores the previous commit, dependencies, and plist if any step fails.

A failed SHA is recorded in `.deploy/failed-sha` so a bad release cannot cause a
restart loop. A later successful commit is eligible normally. Deployment output is
written to `logs/deploy.out.log` and errors to `logs/deploy.err.log`.

## Install / remove

```bash
uv sync                          # development venv
scripts/install_launchd.sh       # initialize deploy checkout and load all agents
scripts/uninstall_launchd.sh     # unload + remove all agents
```

Then browse to `http://<this-mac>.local:8502/` from any device on the LAN.

Manage / inspect:

```bash
launchctl list | grep vy.inventory     # status
tail -f logs/inventory.err.log         # logs
tail -f logs/deploy.out.log            # CD decisions and deployed SHAs
```

## Remote CLI via the `inventory.sh` wrapper

`inventory.sh` (project root) execs the deployed `.deploy/repo/.venv/bin/inv`
directly — **no `uv`, no arbitrary Python**. Whitelist *this script* for
remote/agent execution instead of `uv`, so the grant covers only inventory commands:

```bash
./inventory.sh take "Coffee" 1 --source agent
./inventory.sh list-actions
```

It can be symlinked anywhere (the symlink resolves back to the project), e.g. for an
allowlist entry or an SSH forced command.

## Reboot behavior

These are **LaunchAgents**: they start at *login*, not boot. On this Mac auto-login
is enabled for `vy` and FileVault is off, so a reboot goes boot → auto-login →
agents load → server up, unattended. If auto-login is ever disabled (or FileVault
turned on), the server won't start until someone logs in — the fix then is a
system LaunchDaemon in `/Library/LaunchDaemons` (same plist plus a `UserName` key,
installed with sudo).

## Troubleshooting

```bash
launchctl list | grep vy.inventory            # "-  1  label" = not running, last exit 1
launchctl print gui/$(id -u)/com.vy.inventory # detailed state, last exit reason
```

- **Crash loop / "address already in use" in `inventory.err.log`**: something else
  holds port 8502 (e.g. a manually started `uv run uvicorn`). Find and kill it —
  `lsof -nP -iTCP:8502 -sTCP:LISTEN` — and launchd will bring the service back.
  Never start the server by hand on 8502 while the agent is loaded.
- **`last exit code = 78: EX_CONFIG` + `penalty box`** in `launchctl print`: launchd
  failed to spawn the binary (typically the venv was being rebuilt at the time) and
  benched the job. Reset with:
  `launchctl bootout gui/$(id -u)/<label>` then
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist`.
- **Every request 500s after a manual deploy-venv `uv sync`**: rebuilding `.venv` pulls site-packages out
  from under the running process. CD avoids this by stopping the service before sync.
  If this is ever done manually inside `.deploy/repo`, restart it:
  `launchctl kickstart -k gui/$(id -u)/com.vy.inventory`.

## Notes
- Edit the absolute paths in the plists / wrapper if the repo lives elsewhere.
- The app and CLI use `inventory.db` in the project dir; override with `INVENTORY_DB`.
- Backups land in `backups/` (keeps the most recent 30).
