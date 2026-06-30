#!/bin/sh
# Stop + remove the inventory launchd agents.
set -eu

AGENTS="$HOME/Library/LaunchAgents"
LABELS="com.vy.inventory com.vy.inventory-backup"

for label in $LABELS; do
    dst="$AGENTS/$label.plist"
    if [ -f "$dst" ]; then
        launchctl unload "$dst" 2>/dev/null || true
        rm -f "$dst"
        echo "removed $label"
    else
        echo "not installed: $label"
    fi
done
