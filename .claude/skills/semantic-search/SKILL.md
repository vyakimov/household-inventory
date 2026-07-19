---
name: semantic-search
description: Find inventory items by meaning with `inv search`, and resolve fuzzy item references when exact names fail.
---

# Semantic item search

`inv search` (remotely: `./inventory.sh search`) finds items by meaning, not just
substrings — "something to clean the bathroom" → Toilet cleaner. Powered by
OpenRouter embeddings when `OPENROUTER_API_KEY` is set; without it (or offline)
results silently degrade to name/alias substring matches, so the command always
works and never errors on network failure.

## Usage

```bash
inv search "snack for movie night"                 # positional or --query
inv search --query "bathroom cleaner" --limit 5
```

Result rows: `{id, item, category, quantity, unit, source, score}`. `source` is
`"like"` (name/alias substring hit, `score` null) or `"semantic"` (cosine score;
only matches ≥ 0.35 are returned). LIKE hits always come first; semantic results
fill the remainder up to `--limit` (default 8).

## Resolving an item you can't name exactly

1. Try the mutation directly (`inv take "TP" 1`) — the resolver already handles
   exact names, aliases, normalized and fuzzy matches.
2. On `resource_not_found`, the envelope's `error.details.suggestions` include
   semantic candidates (`{id, item}`). Pick one, **confirm with the user**, and
   apply with `--id`.
3. Semantic matches are never auto-applied — mutations only proceed on a
   confident lexical match. Don't work around this by guessing.
4. Persist what you learned with `--learn-alias <term>` on the follow-up
   mutation. Learning is best-effort: an alias that collides with another item
   adds a `meta.warnings` entry instead of failing the mutation. Aliases must
   not contain `,` or `;`.
5. `inv catalog` is still the full-dump fallback when search doesn't settle it.

## Operational notes

- The embedding cache (`item_embeddings`) maintains itself: the first search
  after an item add/edit re-embeds only the changed rows. There is no reindex
  command, and none is needed.
- Kill switch: `INVENTORY_SEMANTIC=0`. Model override: `INVENTORY_EMBED_MODEL`
  (default `nvidia/nemotron-3-embed-1b:free`, served via OpenRouter).
- Web UI parallel: the search box falls back to semantic matches (labeled
  "Closest matches") only when a substring search finds nothing.
