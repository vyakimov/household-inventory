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
    params: list = []
    if tab == "low":
        where.append("v.is_low = 1")
    elif tab == "necessities":
        where.append("v.necessity = 1")
    if q:
        where.append("(v.item LIKE ? OR v.aliases LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY c.sort_order, v.item COLLATE NOCASE")
    return [dict(r) for r in conn.execute(" ".join(sql), params)]


def group_by_category(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """Preserve the incoming (category-sorted) order while grouping."""
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it["category"], []).append(it)
    return list(groups.items())


def count_low(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM v_items WHERE is_low = 1").fetchone()["n"]


def catalog(conn: sqlite3.Connection) -> list[dict]:
    """Lean dump for an agent to reason over when resolution fails."""
    return [dict(r) for r in conn.execute(
        "SELECT id, item, aliases, category, unit, quantity FROM items "
        "ORDER BY item COLLATE NOCASE")]


def suggest(conn: sqlite3.Connection, term: str, n: int = 5) -> list[str]:
    names = [r["item"] for r in conn.execute("SELECT item FROM items")]
    keyed = {_norm(name): name for name in names}
    out = []
    for key in difflib.get_close_matches(_norm(term), list(keyed), n=n, cutoff=0.5):
        out.append(keyed[key])
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
