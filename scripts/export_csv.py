"""Export the inventory as CSV to stdout, or to a file path argument."""
import sys

from app import db, exporters


def main() -> None:
    conn = db.connect()
    try:
        csv_text = exporters.export_csv_string(conn)
    finally:
        conn.close()
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", newline="") as f:
            f.write(csv_text)
        print(f"wrote {sys.argv[1]}")
    else:
        sys.stdout.write(csv_text)


if __name__ == "__main__":
    main()
