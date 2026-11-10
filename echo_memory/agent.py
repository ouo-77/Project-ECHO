"""Offline entry point for an adapter serving one authorized audience per DB."""

from pathlib import Path

from . import fulltext, store


def estimate_tokens(text: str) -> int:
    """Offline proxy: four ASCII characters or one non-ASCII character per unit."""
    ascii_count = sum(c.isascii() for c in text)
    return len(text) - ascii_count + (ascii_count + 3) // 4


class AgentMemory:
    def __init__(self, database: Path | str, *, use_fts: bool = False):
        self.database = Path(database).resolve()
        self.use_fts = use_fts
        if use_fts:
            fulltext.enable(self.database)

    def remember(self, kind: str, summary: str, *, origin: str,
                 importance: int | None = None, claim_key: str | None = None,
                 supersedes: str | None = None, source_ref: str | None = None,
                 confidence: float | None = None) -> str:
        """Write an explicit summary. For single-valued facts supply a stable key."""
        score = store.DEFAULT_SCORES.get(kind, 3) if importance is None else importance
        return store.add(self.database, kind=kind, content=summary, importance=score,
                         origin=origin, claim_key=claim_key, supersedes=supersedes,
                         source_ref=source_ref, confidence=confidence)

    def reflect(self, experience_id: str, lesson: str, *, origin: str,
                source_ref: str | None = None, confidence: float | None = None) -> str:
        """Attach a supplied, reviewable lesson to the experience that supports it."""
        return store.add(self.database, kind="reflection", content=lesson, origin=origin,
                         importance=store.DEFAULT_SCORES["reflection"], experience_id=experience_id,
                         source_ref=source_ref, confidence=confidence)

    def reflections_for(self, experience_id: str) -> list[dict]:
        return store.reflections_for(self.database, experience_id)

    def recall(self, query: str, *, limit: int = 3, max_chars: int = 600,
               max_tokens: int | None = None) -> str:
        """Return a bounded excerpt with record IDs. Empty searches yield no context."""
        if not 1 <= max_chars <= 4096:
            raise ValueError("max_chars must be between 1 and 4096")
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens < 1):
            raise ValueError("max_tokens must be a positive integer")
        if not query.strip():
            return ""
        lines = []
        remaining = max_chars
        search = fulltext.search if self.use_fts else store.search
        for row in search(self.database, query.strip(), limit):
            prefix = f"[{row['id']}] "
            if remaining <= len(prefix):
                break
            line = prefix + row["content"][:remaining - len(prefix)]
            if max_tokens is not None:
                prior = "\n".join([*lines, ""])
                low, high = 0, len(line)
                while low < high:
                    mid = (low + high + 1) // 2
                    if estimate_tokens(prior + line[:mid]) <= max_tokens:
                        low = mid
                    else:
                        high = mid - 1
                if low <= len(prefix):
                    break
                line = line[:low]
            lines.append(line)
            remaining -= len(line) + 1
        return "\n".join(lines)
