# Deploying on macOS (launchd)

Runs Uvicorn bound to the LAN on port 8502, kept alive by launchd, plus a daily
backup job. No auth — reachable only on the LAN / over WireGuard (v1). The plists
call the venv binaries directly (`.venv/bin/uvicorn`, `.venv/bin/python`), so the
service doesn't depend on `uv` at runtime — but `uv sync` must have created `.venv`.

## Install / remove

```bash
uv sync                          # ensure .venv exists
scripts/install_launchd.sh       # copy plists, create logs/, load both agents
scripts/uninstall_launchd.sh     # unload + remove both agents
```

Then browse to `http://<this-mac>.local:8502/` from any device on the LAN.

Manage / inspect:

```bash
launchctl list | grep vy.inventory     # status
tail -f logs/inventory.err.log         # logs
```

## Remote CLI via the `inventory.sh` wrapper

`inventory.sh` (project root) execs `.venv/bin/inv` directly — **no `uv`, no
arbitrary Python**. Whitelist *this script* for remote/agent execution instead of
`uv`, so the grant covers only inventory commands:

```bash
./inventory.sh take "Coffee" 1 --source agent
./inventory.sh list-actions
```

It can be symlinked anywhere (the symlink resolves back to the project), e.g. for an
allowlist entry or an SSH forced command.

## Notes
- Edit the absolute paths in the plists / wrapper if the repo lives elsewhere.
- The app and CLI use `inventory.db` in the project dir; override with `INVENTORY_DB`.
- Backups land in `backups/` (keeps the most recent 30).
