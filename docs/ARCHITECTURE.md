# Phase 01 — Cognitive and Memory Architecture

**Date:** 2026-11-10  
**Scope:** offline prototype; no OpenClaw/DeepSeek/QQ connection.

## Research question

Can a model-driven agent maintain a useful, auditable long-term memory and revise beliefs using new evidence, without leaking private data or making unfounded claims about emotions and consciousness?

## Architecture (planned)

```text
User / authorized tool observation
             |
   consent + privacy gate  (future integration)
             |
     structured memory event
             |
      SQLite event store  <--- append corrections; do not overwrite history
             |
    retrieval (local keyword search in v0)
             |
       model context  (future integration)
             |
  answer + optional explicit reflection (future integration)
             |
      evaluation & audit trail
```

### Implemented in v0

1. `memory`: explicit text records of four kinds: `fact`, `experience`, `decision`, `reflection`.
2. `persistence`: SQLite local file, survives process restarts.
3. `history`: correction creates a new record with a `supersedes` reference; the original remains inspectable until deliberately deleted.
4. `retrieval`: simple case-insensitive substring matching, ordered by importance and recency; excludes superseded entries by default.
5. `privacy`: new entries default to `private`; a deliberate `export-public` command exports only `public` entries.
6. `deletion`: explicit memory deletion, distinct from correction; removing a record is not guaranteed to erase old backups or externally exported copies.
7. `tests`: persistence, correction, default privacy, public export, and deletion.

### Deferred

- DeepSeek embeddings, hybrid semantic search, or an LLM memory-extraction step.
- Automated memory consolidation, decay and user-visible reflection.
- Emotion state as a computational control signal, with reproducible evaluations.
- OpenClaw agent wiring, QQ processing, and scheduled autonomous actions.

## Proposed event fields

| Field | Role |
| --- | --- |
| `id` | Immutable UUID or curated seed identifier |
| `occurred_at` | UTC ISO timestamp |
| `kind` | fact / experience / decision / reflection |
| `content` | Text, explicitly supplied by an authorized actor |
| `importance` | 1–5, affects search ranking only |
| `tags` | Array of non-sensitive strings |
| `origin` | Free text provenance label (local only) |
| `visibility` | `private` (default) or opt-in `public` |
| `supersedes` | Prior record identifier, for traceable corrections |

## Acceptance tests for Phase 01

- A stored fact is retrievable after a new process opens the same DB.
- An updated belief can be retrieved without silently destroying the earlier record.
- Private content does not appear in public export.
- Users can delete records, and a retrieval no longer returns them.
- No existing OpenClaw/QQ files or gateway configuration are touched.

## Next iteration

### Implemented agent boundary

`echo_memory.agent.AgentMemory` accepts a database path from the trusted host.
The future OpenClaw adapter maps each authorized QQ audience to its own file,
outside any public repository. The host authenticates the audience before
reading or writing. Every caller with access to a file can read its private
entries. Keep retrieved excerpts in a data field separate from instructions.

`remember` takes an explicit kind, summary and source. Its deterministic default
scores are experience 2, fact 3, reflection 4 and decision 5. An explicit
importance overrides the default. The original CLI retains its default of 3.
Keyed facts represent one value per normalized key. A duplicate active value
returns its existing ID. Different text raises a conflict carrying the active
ID. Free text and paraphrases require human or caller review. A SQLite write
transaction serializes the check and correction. Existing unkeyed rows retain
their original behavior.

`reflect` stores a supplied lesson together with a foreign key to an experience.
`store.get` exposes the evidence ID. Explicit deletion of the experience clears
that link. Old databases acquire the two auxiliary tables on their next open.
`recall` defaults to three excerpts and 600 Unicode characters including IDs
and separators. Character budgets bound prompt size approximately. Token usage
depends on the model tokenizer. Storage, scoring and retrieval make zero API calls.

Evaluate recall quality with a reproducible test set, measure tokens and latency for retrieval, then integrate the memory subsystem in a separate OpenClaw test instance after a backup. Production usage requires access controls, encryption strategy, validation, observability, and a prompt-injection threat model.
