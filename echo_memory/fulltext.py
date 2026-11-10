"""Opt-in FTS5 trigram index for Chinese and other unsegmented text."""

from . import store


def enable(path):
    """Backfill once and maintain the index in the same transaction as each write."""
    store.init(path)
    with store.connection(path) as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_fts'").fetchone():
            return
        con.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(id UNINDEXED, content, tokenize='trigram')")
        con.execute("INSERT INTO memory_fts SELECT id, content FROM memories")
        con.execute("""CREATE TRIGGER memory_fts_add AFTER INSERT ON memories BEGIN
            INSERT INTO memory_fts VALUES (new.id, new.content); END""")
        con.execute("""CREATE TRIGGER memory_fts_remove AFTER DELETE ON memories BEGIN
            DELETE FROM memory_fts WHERE id = old.id; END""")
        con.execute("""CREATE TRIGGER memory_fts_update AFTER UPDATE OF content, id ON memories BEGIN
            UPDATE memory_fts SET id = new.id, content = new.content WHERE id = old.id; END""")


def search(path, query, limit):
    """AND across literal whitespace-separated terms. Short terms use a substring filter."""
    with store.connection(path) as con:
        return search_on(con, query, limit)


def search_on(con, query, limit):
    """Use the caller's read snapshot and optional SQLite deadline."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    terms = query.split()
    if not terms:
        return []
    indexed = [term for term in terms if len(term) >= 3]
    source = "memories m"
    clauses = ["NOT EXISTS (SELECT 1 FROM memories n WHERE n.supersedes = m.id)"]
    params = []
    order = "m.importance DESC, m.occurred_at DESC, m.id DESC"
    if indexed:
        source += " JOIN memory_fts ON memory_fts.id = m.id"
        clauses.append("memory_fts MATCH ?")
        params.append(" AND ".join('"' + term.replace('"', '""') + '"' for term in indexed))
        order = "memory_fts.rank, " + order
    for term in terms:
        if len(term) < 3:
            clauses.append("instr(lower(m.content), lower(?)) > 0")
            params.append(term)
    sql = f"SELECT m.* FROM {source} WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ?"
    return [store._to_dict(row) for row in con.execute(sql, (*params, limit))]
