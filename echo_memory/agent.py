"""Offline entry point for an adapter serving one authorized audience per DB."""

from pathlib import Path

from . import store


class AgentMemory:
    def __init__(self, database: Path | str):
        self.database = Path(database).resolve()

    def remember(self, kind: str, summary: str, *, origin: str,
                 importance: int | None = None, claim_key: str | None = None,
                 supersedes: str | None = None) -> str:
        """Write an explicit summary. For single-valued facts supply a stable key."""
        score = store.DEFAULT_SCORES.get(kind, 3) if importance is None else importance
        return store.add(self.database, kind=kind, content=summary, importance=score,
                         origin=origin, claim_key=claim_key, supersedes=supersedes)

    def reflect(self, experience_id: str, lesson: str, *, origin: str) -> str:
        """Attach a supplied, reviewable lesson to the experience that supports it."""
        return store.add(self.database, kind="reflection", content=lesson, origin=origin,
                         importance=store.DEFAULT_SCORES["reflection"], experience_id=experience_id)

    def recall(self, query: str, *, limit: int = 3, max_chars: int = 600) -> str:
        """Return a bounded excerpt with record IDs. Empty searches yield no context."""
        if not 1 <= max_chars <= 4096:
            raise ValueError("max_chars must be between 1 and 4096")
        if not query.strip():
            return ""
        lines = []
        remaining = max_chars
        for row in store.search(self.database, query.strip(), limit):
            prefix = f"[{row['id']}] "
            if remaining <= len(prefix):
                break
            line = prefix + row["content"][:remaining - len(prefix)]
            lines.append(line)
            remaining -= len(line) + 1
        return "\n".join(lines)
