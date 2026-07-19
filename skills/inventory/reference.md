# Inventory CLI — less common operations

## Item management

Validate category and unit against the lookup tables first: `inv lookups`.

```bash
inv new "Paper towels" --category "household paper" --unit rolls \
    --qty 4 --threshold 2 --necessity --step 1 --alias "kitchen roll"
inv edit --item "Paper towels" --qty 6 --threshold 3     # any subset of fields
inv edit --item "Paper towels" --rename "Kitchen towels"
inv delete --item "Paper towels" --dry-run               # preview, then rerun without
```

`inv edit --aliases "a; b"` replaces the full alias list; use `inv alias` for
incremental changes.

## Aliases

```bash
inv alias list --item "toilet paper"
inv alias add  --item "toilet paper" --value "TP"
inv alias rm   --item "toilet paper" --value "TP"
```

Rules: an alias owned by another item (name or alias, case-insensitive) is
rejected with `invalid_arguments`; aliases must not contain `,` or `;`; adding a
duplicate or the item's own name is a silent no-op (no event logged).

## Atomic batch

A JSON array on stdin, applied in one transaction — any failure rolls back the
whole batch and the error carries the failing `index`:

```bash
echo '[
  {"op": "take", "item": "granola", "qty": 1},
  {"op": "put", "id": 12, "qty": 6},
  {"op": "set", "item": "salt", "qty": 2},
  {"op": "on_the_way", "item": "toilet paper", "value": true},
  {"op": "categorize", "item": "bleach", "category": "cleaning"},
  {"op": "alias_add", "item": "toilet paper", "alias": "TP"}
]' | inv batch --dry-run          # then rerun without --dry-run
```

Quantity ops: `take|put|adjust|set`. Required keys never silently default.

## Categories and lookups

```bash
inv lookups                                  # valid categories + units
inv category list
inv category add "spices" --sort-order 5     # existing name + --sort-order reorders
inv category rm "spices"                     # refuses if in use (conflict)
```

## History

```bash
inv log --limit 20                 # recent change events (op, delta, before/after, source)
inv log --item granola             # filtered to one item
```

## Semantic search configuration

- Enabled by `OPENROUTER_API_KEY` in the server's `.env`; when unset or offline,
  `search` and suggestions silently degrade to substring matching.
- Only matches with cosine score ≥ 0.35 are returned; LIKE hits always rank first.
- Kill switch: `INVENTORY_SEMANTIC=0`. Model: `INVENTORY_EMBED_MODEL` (default
  `nvidia/nemotron-3-embed-1b:free`, served via OpenRouter).
- The embedding cache maintains itself — the first search after an item change
  re-embeds only the changed rows. There is no reindex command.

## Envelope details

Exit codes mirror `error.type`: `ok` 0, `internal_error` 1, `resource_not_found`
2, `ambiguous_match` 3, `invalid_arguments` 4, `conflict` 5 — but branch on
`error.type`, not the number. Quantity ops accept `--unit`; a mismatch with the
item's unit proceeds with a `meta.warnings` entry. `--pretty` indents the
envelope; `--source <name>` tags the audit trail (default `cli`).
