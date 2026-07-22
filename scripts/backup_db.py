"""Timestamped SQLite backup into backups/, pruning to the most recent KEEP."""
from datetime import datetime

from app import exporters, settings

KEEP = 30


def main() -> None:
    backups = settings.DB_PATH.parent / "backups"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backups / f"inventory-{ts}.db"
    exporters.backup_to(dest)
    existing = sorted(backups.glob("inventory-*.db"))
    for old in existing[:-KEEP]:
        old.unlink()
    print(f"backup -> {dest}  (kept {len(existing[-KEEP:])})")


if __name__ == "__main__":
    main()
