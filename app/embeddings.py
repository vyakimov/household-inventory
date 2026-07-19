"""Lazy OpenRouter embedding cache and semantic item search."""
import hashlib
import math
import os
import sqlite3
import struct

import httpx

from . import db, queries, settings

EMBEDDINGS_DDL = """
CREATE TABLE IF NOT EXISTS item_embeddings (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    text_hash  TEXT NOT NULL,
    vector     BLOB NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


class EmbeddingsUnavailable(Exception):
    """The optional embedding provider could not provide usable vectors."""


def _api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def _model() -> str:
    return os.environ.get("INVENTORY_EMBED_MODEL", settings.EMBED_MODEL).strip()


def _url() -> str:
    return os.environ.get("INVENTORY_EMBED_URL", settings.EMBED_URL).strip()


def enabled() -> bool:
    """Whether semantic search has credentials and has not been disabled."""
    return os.environ.get("INVENTORY_SEMANTIC") != "0" and bool(_api_key())


def _request_embeddings(texts: list[str]) -> list[list[float]]:
    """Request one batch of embeddings from OpenRouter."""
    if not texts or not enabled():
        raise EmbeddingsUnavailable("semantic search is not configured")
    try:
        response = httpx.post(
            _url(),
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
            },
            json={"model": _model(), "input": texts},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()["data"]
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("response did not contain one vector per input")
        ordered = sorted(data, key=lambda row: row["index"])
        if [row["index"] for row in ordered] != list(range(len(texts))):
            raise ValueError("response indexes did not match inputs")
        vectors = [[float(value) for value in row["embedding"]] for row in ordered]
        if any(not vector or any(not math.isfinite(value) for value in vector) for vector in vectors):
            raise ValueError("response included an invalid vector")
        return vectors
    except Exception as e:  # Provider/network/parse failures are always optional.
        raise EmbeddingsUnavailable(str(e)) from e


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(EMBEDDINGS_DDL)


def _item_text(row: dict) -> str:
    return f"{row['item']}; {', '.join(queries.split_aliases(row['aliases']))}; {row['category']}"


def _text_hash(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\0{text}".encode()).hexdigest()


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    if not blob or len(blob) % 4:
        raise EmbeddingsUnavailable("cached vector is invalid")
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def sync(conn: sqlite3.Connection) -> int:
    """Reconcile the derived cache, embedding only missing or stale item rows."""
    if not enabled():
        return 0
    _ensure_table(conn)
    model = _model()
    items = [dict(row) for row in conn.execute(
        "SELECT id, item, aliases, category FROM items ORDER BY id"
    )]
    cached = {
        row["item_id"]: dict(row)
        for row in conn.execute("SELECT item_id, model, text_hash FROM item_embeddings")
    }
    stale = []
    for item in items:
        text = _item_text(item)
        text_hash = _text_hash(model, text)
        cache = cached.get(item["id"])
        if cache is None or cache["model"] != model or cache["text_hash"] != text_hash:
            stale.append((item, text_hash, text))
    vectors = _request_embeddings([row[2] for row in stale]) if stale else []
    def write_cache() -> None:
        conn.execute("DELETE FROM item_embeddings WHERE item_id NOT IN (SELECT id FROM items)")
        for (item, text_hash, _), vector in zip(stale, vectors, strict=True):
            conn.execute(
                "INSERT INTO item_embeddings(item_id, model, text_hash, vector, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(item_id) DO UPDATE SET model = excluded.model, "
                "text_hash = excluded.text_hash, vector = excluded.vector, "
                "updated_at = excluded.updated_at",
                (item["id"], model, text_hash, _pack(vector)),
            )

    if conn.in_transaction:
        write_cache()
    else:
        with db.transaction(conn):
            write_cache()
    return len(stale)


def _cosine(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right):
        return None
    left_size = math.sqrt(sum(value * value for value in left))
    right_size = math.sqrt(sum(value * value for value in right))
    if not left_size or not right_size:
        return None
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_size * right_size)


def semantic_search(
    conn: sqlite3.Connection, query: str, top_k: int = 8, min_score: float = 0.35
) -> list[dict]:
    """Return nearest cached items, or no results when the optional service fails."""
    if not enabled() or not query.strip() or top_k <= 0:
        return []
    try:
        sync(conn)
        query_vector = _request_embeddings([query])[0]
        rows = conn.execute("SELECT item_id, vector FROM item_embeddings").fetchall()
        scored = []
        for row in rows:
            score = _cosine(query_vector, _unpack(row["vector"]))
            if score is not None and score >= min_score:
                item = queries.get_item(conn, row["item_id"])
                if item is not None:
                    scored.append({"id": item["id"], "item": item["item"], "score": score})
        return sorted(scored, key=lambda row: row["score"], reverse=True)[:top_k]
    except Exception:
        return []
