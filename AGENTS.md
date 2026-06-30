# AGENTS.md

Canonical guidance for AI agents and contributors working in this repo. A phone-first
household inventory web app: FastAPI + HTMX + Tailwind over a single SQLite file, plus
an agent-facing `inv` CLI. See `README.md` for the user-facing overview and quickstart.

## Environment & commands

Managed with **uv** (Python 3.13, pinned in `.python-version`). The project installs as
a package, exposing the `inv` console script.

```bash
uv sync                                   # venv + deps
uv run pytest -q                          # 37 tests — keep them green
uv run ruff check .                        # lint (config in pyproject.toml)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8502   # serve
uv run python scripts/init_db.py          # create schema + seed lookups
uv run inv list-actions                    # the agent CLI (or ./inventory.sh ...)
```

Run tests and ruff after any change. Match the surrounding style: raw SQL with small
helper functions, no ORM, type hints, terse docstrings.

## Architecture

```
app/
  settings.py    config + canonical CATEGORIES/UNITS; loads .env
  db.py          connect(), init_db(), transaction()  — sqlite3, WAL, FKs on
  schema.sql     items, categories, units, events, and the v_items VIEW
  queries.py     read-only queries + resolve()/catalog()/split_aliases()
  mutations.py   all writes; each logs an event; ValidationError on bad input
  exporters.py   CSV string + SQLite backup
  cli.py         `inv` — JSON-envelope CLI for agents
  main.py        FastAPI routes + HTMX partials; Jinja templates/ + static/
scripts/         init_db, import_from_notion, backup_db, export_csv, install/uninstall_launchd
tests/           low-stock logic, mutations, CLI, routes
deploy/          launchd plists + notes
inventory.sh     standalone CLI wrapper (the safe-to-whitelist remote entry point)
```

Layering: `db` → `queries`/`mutations` → `cli`/`main`. `cli.py` and `main.py` share the
same `queries`/`mutations`, so behavior stays identical between web and CLI.

## Invariants — do not break these

- **Mutations assume an open transaction.** Functions in `mutations.py` do *not* call
  BEGIN/COMMIT themselves — the caller wraps them in `with db.transaction(conn):`. This
  is what makes `inv batch` atomic (many mutations, one transaction). Web routes wrap
  each call; the CLI wraps single ops and rolls back on `--dry-run`.
- **Every mutation writes an `events` row** (op, delta, before/after, source, request_id).
  Keep this when adding mutations.
- **`v_items` is the source of truth for status.** It computes `is_low` and `needs_buy`
  over all rows; the filter tabs (`low`/`necessities`/`all`) are just WHERE clauses on
  it. Don't recompute low-stock logic elsewhere.
- **quantity clamps at 0** (never negative). **`on_the_way` never auto-clears** on
  restock — it's manual only.
- **category/unit are FK lookup tables** (`categories`, `units`), seeded from the Notion
  enums. Validate against them (`mutations._validate_category/_unit`); don't invent values.
- **Aliases split on both `,` and `;`** — always use `queries.split_aliases()`, never a
  bare `.split(",")`.
- **Auth is intentionally absent (v1).** Security is LAN-only + WireGuard. Do not add a
  half-built auth layer; that's a deliberate v2 item.

## The `inv` CLI contract

Built to the `llm-cli-skill` conventions (github.com/vyakimov/llm-cli-skill):

- **stdout is one JSON envelope, nothing else**; diagnostics/warnings go to **stderr**.
  Success: `{ok, action, result, meta}`. Failure: `{ok, action, error:{type, message, details}}`.
- **`error.type`** is the machine contract (snake_case): `resource_not_found`,
  `ambiguous_match`, `invalid_arguments`, `conflict`, `permission_denied`,
  `internal_error`. Exit code mirrors it (`EXIT` map); agents branch on `error.type`.
- **Resolution is tiered** (exact name → exact alias → normalized → fuzzy). One confident
  match proceeds; several → `ambiguous_match` (refuse, list candidates); none →
  `resource_not_found` with a hint to run `inv catalog` and apply by `--id`.
- **Idempotent** relative ops via `--request-id` (deduped through `events`); **`--dry-run`**
  on all mutations; **`--learn-alias`** persists a resolved alias.
- argparse gotcha: global flags (`--db`, `--pretty`) use `default=argparse.SUPPRESS` and
  are read via `getattr` so the subparser can't clobber a value parsed before the
  subcommand. Keep that pattern if you add global flags.

## `inventory.sh` (remote/whitelist boundary)

Execs `.venv/bin/inv` directly — **no `uv`, no arbitrary Python** — and **pins the DB**
(exports `INVENTORY_DB`, rejects any `--db` arg). Whitelist this script for remote/agent
execution, never `uv`. Don't weaken these guarantees.

## Frontend

Deliberate "enamel kitchen" design — **do not regress to generic gray Tailwind.** Palette,
fonts, and the signature **stock gauge** (left card edge, fills quantity-vs-threshold) are
defined in `tailwind.config` inline in `base.html` plus `app/static/app.css`. Tailwind +
HTMX load from CDN (v1). Preserve HTMX target ids (`item-{id}`, `item-list`,
`inventory-body`, `admin-tbody`) and form field names (`q`, `tab`, `quantity`, `value`)
when editing templates — routes and tests depend on them.

## Secrets & data

- `NOTION_TOKEN` and `NOTION_DATABASE_ID` come from `.env` (gitignored; see `.env.example`)
  and are used only by the one-time importer — never by the running app.
- **Never commit** `inventory.db`, `*.db-wal/-shm`, `backups/`, or `.env`. They're
  gitignored; keep them that way. No real inventory data belongs in source (tests use
  invented fixtures).
