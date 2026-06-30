#!/bin/sh
# inventory.sh — single fixed entry point for the household inventory CLI.
#
# Execs the venv console-script `inv` directly: no `uv`, no arbitrary Python.
# Whitelisting THIS file grants only inventory commands, never general code
# execution. It may be symlinked anywhere (the symlink is resolved back here).
#
#   ./inventory.sh take "Coffee" 1 --source agent
#   ./inventory.sh list-actions

set -eu

# Resolve this script's real location (following symlinks), portably on macOS.
SOURCE="$0"
while [ -h "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    case "$SOURCE" in
        /*) : ;;
        *) SOURCE="$DIR/$SOURCE" ;;
    esac
done
DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"

INV="$DIR/.venv/bin/inv"
if [ ! -x "$INV" ]; then
    echo '{"ok": false, "action": "wrapper", "error": {"type": "internal_error", "message": "inventory venv not found; run `uv sync` in the project directory"}}' >&2
    exit 1
fi

# Lock to the production database: refuse any attempt to redirect it, so a remote
# (whitelisted) caller can only ever touch this household's inventory.
for arg in "$@"; do
    case "$arg" in
        --db|--db=*)
            echo '{"ok": false, "action": "wrapper", "error": {"type": "permission_denied", "message": "--db is not allowed via inventory.sh; the database is pinned"}}' >&2
            exit 1
            ;;
    esac
done
export INVENTORY_DB="$DIR/inventory.db"

exec "$INV" "$@"
