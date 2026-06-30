"""Create the database schema and seed the category/unit lookup tables."""
from app import db, settings


def seed_lookups(conn) -> None:
    with db.transaction(conn):
        for i, name in enumerate(settings.CATEGORIES):
            conn.execute(
                "INSERT OR IGNORE INTO categories(name, sort_order) VALUES (?, ?)",
                (name, i),
            )
        for name in settings.UNITS:
            conn.execute("INSERT OR IGNORE INTO units(name) VALUES (?)", (name,))


def main() -> None:
    conn = db.connect()
    db.init_db(conn)
    seed_lookups(conn)
    cats = conn.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"]
    units = conn.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"]
    items = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    conn.close()
    print(f"Initialized {settings.DB_PATH}")
    print(f"  categories: {cats}")
    print(f"  units:      {units}")
    print(f"  items:      {items}")


if __name__ == "__main__":
    main()
