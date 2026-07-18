---
name: verify
description: Run and drive the inventory app against a scratch database to verify changes end-to-end.
---

# Verifying changes

Never point a test server at the repo's `inventory.db` (live household data). Use a
scratch DB via `INVENTORY_DB`:

```bash
export INVENTORY_DB=/tmp/scratch/inventory.db   # any throwaway path
uv run python scripts/init_db.py                # schema + lookup seeds
uv run inv new "Granola" --category food --qty 1 --threshold 1 --necessity
INVENTORY_DB=... uv run uvicorn app.main:app --host 127.0.0.1 --port 8599
```

Drive it with curl; the UI is plain HTMX so response fragments/headers are the surface:

- Card mutations: `curl -X POST :8599/items/1/inc` → returns the refreshed card HTML;
  crossing the low-stock threshold adds an `HX-Trigger: low-changed` response header.
- Filter/search partials: `GET /partials/inventory?tab=low&q=...`, `GET /partials/list`.
- Admin edit: `POST /items/{id}/edit` with form fields (422 + plain text on validation error).
- CSV import: `curl -F "file=@x.csv;type=text/csv" :8599/import/csv` → styled result
  fragment (success or "Import aborted"); takes a pre-import backup into `backups/`
  next to the DB.
- CLI: `INVENTORY_DB=... uv run inv <cmd>` — assert on the JSON envelope and exit code
  (`ok` → 0; `error.type` mirrors the EXIT map in `app/cli.py`).

Gotchas:
- `uv run inv` must run from the repo root (the venv lives here).
- The CLI writes to the same DB as the server; WAL handles the concurrency.
- Kill the server with `pkill -f "uvicorn app.main:app.*8599"`.
