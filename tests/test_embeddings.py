from app import embeddings


def _vector_for(text: str) -> list[float]:
    text = text.lower()
    if "bathroom" in text or "bleach" in text or "cleaner" in text or "cleaning" in text:
        return [1.0, 0.0]
    if "toilet" in text or "paper" in text:
        return [0.7, 0.7]
    if "snack" in text or "chips" in text or "granola" in text:
        return [0.0, 1.0]
    return [0.1, 0.9]


def _add_cleaner(conn):
    conn.execute(
        "INSERT INTO items(item, aliases, category, unit) VALUES (?, ?, ?, ?)",
        ("Bathroom bleach", "disinfectant", "cleaning", "units"),
    )


def test_sync_only_embeds_stale_items_and_model_changes(conn, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = []

    def fake(texts):
        calls.append(texts)
        return [_vector_for(text) for text in texts]

    monkeypatch.setattr(embeddings, "_request_embeddings", fake)
    assert embeddings.sync(conn) == 6
    assert conn.execute("SELECT COUNT(*) FROM item_embeddings").fetchone()[0] == 6
    assert len(calls) == 1

    assert embeddings.sync(conn) == 0
    assert len(calls) == 1

    conn.execute("UPDATE items SET aliases = 'breakfast cereal' WHERE item = 'Granola'")
    assert embeddings.sync(conn) == 1
    assert len(calls) == 2 and len(calls[-1]) == 1

    conn.execute("UPDATE items SET item = 'Oat granola' WHERE item = 'Granola'")
    assert embeddings.sync(conn) == 1
    assert len(calls) == 3 and len(calls[-1]) == 1

    monkeypatch.setenv("INVENTORY_EMBED_MODEL", "test-model-v2")
    assert embeddings.sync(conn) == 6
    assert len(calls) == 4 and len(calls[-1]) == 6


def test_semantic_search_ranks_and_applies_cutoff(conn, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _add_cleaner(conn)
    monkeypatch.setattr(
        embeddings, "_request_embeddings", lambda texts: [_vector_for(text) for text in texts]
    )

    matches = embeddings.semantic_search(conn, "cleaner for bathroom", min_score=0.7)
    assert [match["item"] for match in matches[:2]] == ["Bathroom bleach", "Toilet paper"]
    assert embeddings.semantic_search(conn, "cleaner for bathroom", min_score=0.8) == [
        matches[0]
    ]


def test_disabled_semantic_search_never_calls_provider(conn, monkeypatch):
    called = False

    def fake(texts):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(embeddings, "_request_embeddings", fake)
    assert embeddings.semantic_search(conn, "bathroom cleaner") == []
    assert called is False


def test_unavailable_provider_degrades_to_no_results(conn, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def unavailable(texts):
        raise embeddings.EmbeddingsUnavailable("offline")

    monkeypatch.setattr(embeddings, "_request_embeddings", unavailable)
    assert embeddings.semantic_search(conn, "bathroom cleaner") == []
