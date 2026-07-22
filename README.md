# Household Inventory

A phone-first web app to replace the Notion household-inventory database. FastAPI + HTMX + Tailwind over a single SQLite file, plus an agent-friendly CLI.

## Status

v1 built and tested: 123 items imported from Notion (need-to-buy reconciles 7/7),
phone-first UI with the four filter tabs + steppers, agent CLI, tests green.

## Quickstart

```bash
uv sync                                   # create venv + install
uv run python scripts/init_db.py          # create schema + seed lookups
cp .env.example .env                          # then set NOTION_TOKEN + NOTION_DATABASE_ID
uv run python scripts/import_from_notion.py   # pull live Notion data (optional)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8502   # serve on the LAN

uv run pytest -q                          # run the test suite
uv run ruff check .                        # lint
./inventory.sh list-actions                # agent CLI via the standalone wrapper
```

`inventory.sh` execs the deployed venv's `inv` console script directly (no `uv`, no
arbitrary Python) — whitelist it for remote/agent use. For always-on macOS deployment and the
launchd install/remove scripts see [deploy/README.md](deploy/README.md).

## Project layout

```
inventory.sh   standalone CLI wrapper (safe to whitelist remotely)
app/           settings, db, schema.sql, queries, mutations, exporters, cli, main, templates/, static/
scripts/       init_db, import_from_notion, backup_db, export_csv, install_launchd, uninstall_launchd
tests/         low-stock logic, queries, mutations, exporters, CLI, routes
deploy/        launchd plists + CI-gated continuous-deployment notes
skills/        how agents operate the inventory (common commands in SKILL.md, the rest in reference.md)
```

## v1 scope

- **Phone-first inventory page** at `/` with filter tabs **Low stock | To buy | Necessities | All** (default Low stock) and live search. The To buy tab is the shopping list: low-stock necessities not already marked on the way. No separate restock or quick-update screens.
- **Quantity steppers** (`−` / value / `+`) on every card, in every view, regardless of stock level. Fractional quantities handled via a per-item `step` column (default 1, e.g. 0.1 for bag-fractions). Plus "set exact" and "add N".
- **`On the way`** toggle (never auto-clears).
- **Admin page** for full-field edit / add / delete (category + unit dropdowns from lookup tables).
- **History page** at `/history` — read-only view of the `events` audit log (who changed what, when, from which source), grouped by day.
- **Import/export**: one-time Notion import (CLI), CSV export + CSV import (web upload; upserts by name, atomic, takes a pre-import backup), downloadable SQLite backup.
- **Agent CLI** for safe natural-language-driven updates (see below).
- **Auth: none.** Security boundary is the LAN + WireGuard. (Auth is a v2 item.)

## Stack

Python 3.11+, FastAPI, Uvicorn, Jinja2, HTMX, Tailwind, stdlib `sqlite3` (raw SQL — no SQLAlchemy), pytest + httpx, ruff.

Semantic search augments the normal name-and-alias search only when it finds no lexical
matches, so requests such as “something to clean the bathroom” can still find relevant
items. Set `OPENROUTER_API_KEY` to enable it; `INVENTORY_EMBED_MODEL` optionally selects
the embedding model (default `nvidia/nemotron-3-embed-1b:free`), and `INVENTORY_EMBED_URL`
can override the OpenRouter-compatible endpoint.

## Data model

- `items` — canonical item, aliases, category (FK), quantity (REAL, ≥0), unit (FK), `step`, `low_stock_threshold` (`-1` never, `0` when empty, positive at/below threshold), `necessity`, `on_the_way`, `shopping_item_name`, `notes`, timestamps.
- `categories` / `units` — FK-enforced lookup tables. Categories use a household-oriented taxonomy and can be managed through `inv category`; units are seeded from the live Notion enums (packs, cans, cartons, kg, jars, bottles, blocks, boxes, rolls, bags, tubes, containers, buckets, pouches, units, mixed, unclear, other, g).
- `v_items` — view over all rows computing `is_low` and `needs_buy`; the filter tabs are `WHERE` clauses on it.
- `events` — append-only audit log written by both web and CLI mutations (op, delta, before/after, source, note, timestamp, nullable unique `request_id` for CLI idempotency).

## Routes (no auth in v1)

Pages: `GET /` (`?tab=`, `?q=`), `GET /history`, `GET /admin`, `GET /import-export`.
Partials/actions: `GET /partials/inventory`, `GET /partials/list`, `GET /partials/item/{id}`, `POST /items/{id}/inc|dec|quantity|on-the-way`, `GET /partials/admin-row/{id}[/edit]`, `POST /items/{id}/edit|delete`, `POST /items`.
Import/export: `POST /import/csv`, `GET /export/csv`, `GET /backup/sqlite`.

## Agent CLI (`app/cli.py`)

Shares `mutations.py`/`queries.py` with the web app; same SQLite file (WAL handles concurrent web+CLI access). The CLI is the safe structured surface — the agent never writes SQL. **Built to the `llm-cli-skill` conventions** ([github.com/vyakimov/llm-cli-skill](https://github.com/vyakimov/llm-cli-skill)); apply that skill when implementing and reviewing it.

Commands: `inv take|put|set|on-the-way|get|search|new|edit|delete|batch|catalog|lookups|category|alias|log|list|list-actions`

**Output contract (per skill):**
- **stdout is JSON only** — one envelope per call, no banners; diagnostics/progress/warnings go to **stderr**; `--pretty` indents.
- Envelope: `{"ok": true, "action": "take", "result": {…}, "meta": {…}}` on success; `{"ok": false, "action": "take", "error": {"type": "…", "message": "…", "details": {…}}}` on failure.
- `error.type` is the machine contract (snake_case): `resource_not_found`, `ambiguous_match`, `invalid_arguments`, `conflict`, plus `authentication_failed`/`upstream_error`/`timeout` (importer/network) and `internal_error`. Exit `0` on ok, nonzero otherwise (value mirrors the type; agents branch on `error.type`, not the number).
- **Self-describing:** `inv list-actions` returns the action list with params + descriptions; `--help` per command.
- **Stateless / idempotent / dry-run:** every call self-contained; `set`/`on-the-way` are idempotent; relative `take`/`put` accept an optional `--request-id` (deduped via `events`; a replay returns the original result with `meta.idempotent_replay=true`) so retries can't double-apply; all mutating commands support `--dry-run`. Flags: `--source agent`, `--id`, `--pretty`.

**Resolution tiers:** exact canonical → exact alias → normalized → fuzzy (confidence threshold). Unique hit proceeds; multiple above threshold → `ambiguous_match` (candidates in `error.details`); none → `resource_not_found` with suggestions that include IDs. `--id` bypasses resolution, and `--item` is accepted for item-bearing commands so agents can avoid positional ambiguity.

**Lookup/list helpers:** `inv lookups` returns valid categories + units for item creation/editing. `inv category add|rm|list` manages the category lookup table while refusing to delete in-use values; adding an existing category with `--sort-order` reorders it. `inv list --tab needs-buy` lists low-stock necessities that are not already marked on the way.

**Atomic batch:** `inv batch` applies multiple ops from stdin JSON in one transaction; any failure rolls back the whole batch. Quantity ops use `{"op": "take|put|adjust|set", "item": …|"id": …, "qty": …}`; `on_the_way` uses `"value"`; and metadata migrations can use `{"op": "categorize", "item": …|"id": …, "category": …}` or `{"op": "alias_add", "item": …|"id": …, "alias": …}`. Required keys never silently default. Alias additions reject names already owned by another item, and values containing `,` or `;`.

**Semantic fallback** (e.g. "TP" → "toilet paper" when not a registered alias): on `resource_not_found` the agent runs `inv catalog` (lean JSON dump of all items + aliases), reasons, **confirms with the user**, applies by `--id`, and optionally `--learn-alias <term>` to persist the alias (logged to `events`, reversible via `inv alias rm`). Learning is best-effort: an alias that collides with another item adds a `meta.warnings` entry instead of failing the mutation.

**Location is not modeled in v1** — the agent drops location words ("from the loft").

## Migration

`scripts/import_from_notion.py` reads the Notion DB via the REST API + integration token (MCP query is plan-gated), seeds the lookup tables, upserts items by name, skips the `Low stock`/`Need to buy` formulas (recomputed in `v_items`), and reconciles its computed need-to-buy count against Notion's `Need to buy` formula count. `NOTION_TOKEN` and `NOTION_DATABASE_ID` are read from `.env` (see `.env.example`), used once, never referenced by the running app.

## Deployment

Uvicorn under **launchd** on the host Mac (BabyBjornBorg), bound to the LAN (`0.0.0.0:8502`). Reachable at `http://<host>.local:8502`; WireGuard for remote. Firewall: restrict 8502 to LAN/WireGuard interfaces.

## Backups

Daily SQLite backup (keep last 30), weekly CSV export, and a backup before any bulk import.

## v2 / Later enhancements

- [ ] **Hybrid search over item names and aliases** — combine lexical full-text search (SQLite FTS5 over `item` + `aliases`) with semantic/embedding similarity, so resolution handles synonyms and abbreviations (e.g. "TP" → "toilet paper") directly in search instead of relying on the agent's `catalog` fallback. Should back both the web search box and the CLI resolver.
- [ ] Password/auth — reverse-proxy Basic Auth or app-level FastAPI session login.
- [x] Auto-refilter cards on quantity change (card leaves the Low stock tab live) — done via `HX-Trigger: low-changed`.
- [ ] Location / multi-location stock tracking.
- [ ] Barcode scanning, receipt parsing, purchase history, normalized alias table, offline PWA, predictive restock.
