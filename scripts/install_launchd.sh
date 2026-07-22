#!/bin/sh
# Install + start the inventory web service and daily backup as launchd agents.
set -eu

HERE="$(cd "$(dirname "$0")/.." >/dev/null 2>&1 && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LABELS="com.vy.inventory com.vy.inventory-backup com.vy.inventory-deploy"
DEPLOY="$HERE/.deploy/repo"

mkdir -p "$AGENTS" "$HERE/logs"

if [ ! -d "$DEPLOY/.git" ]; then
    origin="$(git -C "$HERE" remote get-url origin)"
    mkdir -p "$HERE/.deploy"
    git clone --branch main --single-branch "$origin" "$DEPLOY"
fi
if [ -n "$(git -C "$DEPLOY" status --porcelain)" ]; then
    echo "deployment checkout has local changes: $DEPLOY" >&2
    exit 1
fi
git -C "$DEPLOY" fetch --quiet origin main
git -C "$DEPLOY" checkout --detach --force origin/main
touch "$HERE/.deploy/.inventory-deploy"
(cd "$DEPLOY" && uv sync --frozen)

for label in $LABELS; do
    src="$DEPLOY/deploy/$label.plist"
    dst="$AGENTS/$label.plist"
    cp "$src" "$dst"
    launchctl unload "$dst" 2>/dev/null || true
    launchctl load -w "$dst"
    echo "loaded $label"
done

echo "Inventory serving at http://$(hostname)/:8502  (LAN only)"
echo "Logs: $HERE/logs/inventory.err.log"
