"""Read-only queries and item resolution (shared by web and CLI)."""
import difflib
import re
import sqlite3

ALIAS_SEP = re.compile(r"[;,]")


def split_aliases(s: str | None) -> list[str]:
    """Aliases are stored free-form; Notion data mixes ',' and ';' separators."""
    return [a.strip() for a in ALIAS_SEP.split(s or "") if a.strip()]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _stem(token: str) -> str:
    """Return a small, conservative English stem suitable for inventory labels."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        base = token[:-3]
        return base[:-1] if len(base) > 2 and base[-1] == base[-2] else base
    if len(token) > 4 and token.endswith("ed"):
        base = token[:-2]
        return base[:-1] if len(base) > 2 and base[-1] == base[-2] else base
    if len(token) > 4 and token.endswith("es") and token[-3] in "sxz":
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _lexical_score(query: str, label: str) -> float | None:
    """Score exact, substring, stemmed-token, and fuzzy label matches."""
    query_norm = _norm(query)
    label_norm = _norm(label)
    if not query_norm or not label_norm:
        return None
    if query_norm == label_norm:
        return 1.0
    if query_norm in label_norm:
        return 0.95 + 0.04 * len(query_norm) / len(label_norm)

    query_tokens = query_norm.split()
    label_tokens = label_norm.split()
    token_scores = []
    for query_token in query_tokens:
        best = 0.0
        for label_token in label_tokens:
            if query_token == label_token:
                best = 1.0
            elif _stem(query_token) == _stem(label_token):
                best = max(best, 0.96)
            else:
                best = max(best, difflib.SequenceMatcher(None, query_token, label_token).ratio())
        token_scores.append(best)
    if token_scores and min(token_scores) >= 0.74:
        return 0.8 + 0.15 * sum(token_scores) / len(token_scores)

    similarity = difflib.SequenceMatcher(None, query_norm, label_norm).ratio()
    return 0.75 * similarity if similarity >= 0.76 else None


def categories(conn: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in conn.execute(
        "SELECT name FROM categories ORDER BY sort_order, name")]


def units(conn: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM units ORDER BY name")]


def get_item(conn: sqlite3.Connection, item_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM v_items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def list_items(conn: sqlite3.Connection, tab: str = "all", q: str | None = None) -> list[dict]:
    sql = ["SELECT v.* FROM v_items v JOIN categories c ON c.name = v.category"]
    where: list[str] = []
    if tab == "low":
        where.append("v.is_low = 1")
    elif tab == "necessities":
        where.append("v.necessity = 1")
    elif tab == "needs-buy":
        where.append("v.needs_buy = 1")
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY c.sort_order, v.item COLLATE NOCASE")
    rows = [dict(r) for r in conn.execute(" ".join(sql))]
    if not q:
        return rows

    ranked = []
    for position, row in enumerate(rows):
        candidates = [
            (0, _lexical_score(q, row["item"])),
        ]
        candidates.extend(
            (1, _lexical_score(q, alias)) for alias in split_aliases(row["aliases"])
        )
        candidates.append((2, _lexical_score(q, row["category"])))
        matches = [(tier, score) for tier, score in candidates if score is not None]
        if matches:
            tier, score = min(matches, key=lambda match: (match[0], -match[1]))
            ranked.append((tier, -score, position, row))
    ranked.sort(key=lambda match: match[:3])
    return [row for *_, row in ranked]


def group_by_category(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """Preserve the incoming (category-sorted) order while grouping."""
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["category"], []).append(it)
    return list(groups.items())


def count_low(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM v_items WHERE is_low = 1").fetchone()["n"]


def count_needs_buy(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM v_items WHERE needs_buy = 1").fetchone()["n"]


def recent_events(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    """Newest-first audit trail; created_at is UTC, local_time is for display."""
    return [dict(r) for r in conn.execute(
        "SELECT *, datetime(created_at, 'localtime') AS local_time"
        " FROM events ORDER BY id DESC LIMIT ?", (limit,))]


def catalog(conn: sqlite3.Connection) -> list[dict]:
    """Lean dump for an agent to reason over when resolution fails."""
    return [dict(r) for r in conn.execute(
        "SELECT id, item, aliases, category, unit, quantity FROM items "
        "ORDER BY item COLLATE NOCASE")]


def suggest(conn: sqlite3.Connection, term: str, n: int = 5) -> list[dict]:
    rows = [dict(r) for r in conn.execute("SELECT id, item, aliases FROM items")]
    keyed: dict[str, dict] = {}
    for r in rows:
        for label in (r["item"], *split_aliases(r["aliases"])):
            keyed.setdefault(_norm(label), r)
    out, seen = [], set()
    for key in difflib.get_close_matches(_norm(term), list(keyed), n=n, cutoff=0.5):
        row = keyed[key]
        if row["id"] not in seen:
            seen.add(row["id"])
            out.append({"id": row["id"], "item": row["item"]})
    return out


def resolve(conn: sqlite3.Connection, term: str) -> tuple[str, list[dict]]:
    """Tiered resolver. Returns (status, matches); status in {ok, ambiguous, not_found}.

    ok      -> exactly one confident match (matches[0])
    ambiguous -> several plausible matches (caller should refuse and disambiguate)
    not_found -> nothing confident
    """
    rows = [dict(r) for r in conn.execute("SELECT * FROM v_items")]
    t = (term or "").strip()
    if not t:
        return ("not_found", [])
    tl = t.lower()

    # tier 1: exact canonical name (item is UNIQUE, so at most one)
    hits = [r for r in rows if r["item"].lower() == tl]
    if hits:
        return ("ok", hits)

    # tier 2: exact alias
    hits = [r for r in rows if tl in (a.lower() for a in split_aliases(r["aliases"]))]
    if hits:
        return ("ok", hits) if len(hits) == 1 else ("ambiguous", hits)

    # tier 3: normalized substring over name + aliases
    nt = _norm(t)
    hits = [
        r for r in rows
        if nt and (nt in _norm(r["item"])
                   or any(nt in _norm(a) for a in split_aliases(r["aliases"])))
    ]
    if hits:
        return ("ok", hits) if len(hits) == 1 else ("ambiguous", hits)

    # tier 4: fuzzy over normalized name + aliases
    candidates: dict[str, dict] = {}
    for r in rows:
        for label in (r["item"], *split_aliases(r["aliases"])):
            candidates.setdefault(_norm(label), r)
    matched: list[dict] = []
    seen: set[int] = set()
    for key in difflib.get_close_matches(nt, list(candidates), n=5, cutoff=0.72):
        r = candidates[key]
        if r["id"] not in seen:
            seen.add(r["id"])
            matched.append(r)
    if len(matched) == 1:
        return ("ok", matched)
    if len(matched) > 1:
        return ("ambiguous", matched)
    return ("not_found", [])
