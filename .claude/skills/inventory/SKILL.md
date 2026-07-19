---
name: inventory
description: Operate the household inventory (inventory-web) — check stock, record items taken or bought, manage the shopping list, and find items via the `inv` CLI.
---

# Using the household inventory

Run commands with `./inventory.sh <cmd>` (the whitelisted remote entry point; DB
pinned) or `uv run inv <cmd>` inside the repo. Every call prints exactly one JSON
envelope on stdout; diagnostics go to stderr:

- success: `{"ok": true, "action": …, "result": …, "meta": …}`, exit 0
- failure: `{"ok": false, "action": …, "error": {"type", "message", "details"}}` —
  branch on `error.type`: `resource_not_found`, `ambiguous_match`,
  `invalid_arguments`, `conflict`, `internal_error`.

Prefer option forms (`--item`, `--qty`, `--value`) over positionals — they survive
argv quoting better. `--id N` bypasses name resolution entirely.

## Everyday commands

```bash
inv get --item "toilet paper"                 # one item's current state
inv search --query "something for the grill"  # meaning-aware; rows tagged like|semantic
inv list --tab needs-buy                      # the shopping list (low necessities not on the way)
inv list --tab low                            # everything at/below its threshold
inv take --item granola --qty 1               # consumed one (clamps at 0)
inv put --item "cat food" --qty 12            # restocked
inv set --item salt --qty 3                   # correct to an exact count
inv on-the-way --item "toilet paper" --value true   # mark as ordered
```

- Quantities never go negative; `take` reports `clamped: true` when it hit 0.
- `on_the_way` never clears itself on restock — set it back to `false` explicitly.
- Retried `take`/`put` can double-apply: pass `--request-id <uuid>` to make them
  idempotent (a replay returns the original result with `meta.idempotent_replay`).
- Every mutation accepts `--dry-run` to preview without persisting.
- Quantities may be fractional (per-item `step`, e.g. 0.1 bags).

## When a name doesn't resolve

1. Just try the command — the resolver handles exact names, aliases, normalized
   and fuzzy matches. One confident hit proceeds; several return
   `ambiguous_match` with `candidates`; none returns `resource_not_found` with
   `suggestions` (which include semantic matches).
2. Pick from `candidates`/`suggestions`, **confirm with the user**, re-run with
   `--id`. Semantic matches are never auto-applied — don't guess around that.
3. Optionally add `--learn-alias "<term the user said>"` to the follow-up
   mutation so it resolves next time. Learning is best-effort: a collision with
   another item warns in `meta.warnings` instead of failing.
4. Still stuck: `inv catalog` dumps every item + aliases to reason over.
5. Drop location words ("from the loft") — location isn't modeled.

Less common operations — creating/editing/deleting items, aliases, atomic
batches, categories, history, semantic-search configuration, exit codes — are in
[reference.md](reference.md).
