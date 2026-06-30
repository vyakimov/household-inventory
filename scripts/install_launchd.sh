#!/bin/sh
# Install + start the inventory web service and daily backup as launchd agents.
set -eu

HERE="$(cd "$(dirname "$0")/.." >/dev/null 2>&1 && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LABELS="com.vy.inventory com.vy.inventory-backup"

mkdir -p "$AGENTS" "$HERE/logs"

for label in $LABELS; do
    src="$HERE/deploy/$label.plist"
    dst="$AGENTS/$label.plist"
    cp "$src" "$dst"
    launchctl unload "$dst" 2>/dev/null || true
    launchctl load -w "$dst"
    echo "loaded $label"
done

echo "Inventory serving at http://$(hostname)/:8502  (LAN only)"
echo "Logs: $HERE/logs/inventory.err.log"
