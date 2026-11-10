# Project ECHO

> An experiment in persistent memory, self-reflection, and accountable autonomous learning.

**Status:** Phase 01 — persistent-memory prototype (2026-11-10). Not a conscious AI system, not a production-ready autonomous agent.

Project ECHO investigates whether an AI-based agent can gradually improve its behavior through **traceable experience**, rather than a fixed fictional personality. Initial research directions:

- Long-term memory that survives process restarts.
- Learning and corrections without silently rewriting history.
- A testable computational model of internal emotional states (future research, not implemented yet).
- Independent reasoning with evidence-based disagreement and clearly bounded actions.
- Honest self-description and uncertainty about machine consciousness.

The first deliverable is an offline, dependency-free **SQLite memory prototype**. It does not connect to QQ, OpenClaw, or DeepSeek yet. Integration will be considered only after local tests and privacy review.

## Quick start (Python 3.10+)

```powershell
python -m echo_memory init
python -m echo_memory seed --file data/seed_memories.jsonl
python -m echo_memory search "ECHO"
python -m echo_memory add --kind experience --content "Test memory that survives restart" --tags test --importance 3
python -m echo_memory stats
python -m unittest discover -s tests -v
```

The local database defaults to `data/echo_memory.sqlite` and is **excluded from Git**. Override it with `ECHO_MEMORY_DB` if needed. `add` always defaults to `private`.

For command help:

```powershell
python -m echo_memory --help
python -m echo_memory add --help
```

## Repository map

| Path | Purpose |
| --- | --- |
| `docs/ARCHITECTURE.md` | Phase 01 architecture and acceptance criteria |
| `docs/PRIVACY.md` | Public/private data rules |
| `journal/000-2026-11-09.md` | Public, de-identified project inception record |
| `journal/001-2026-11-10.md` | First implementation milestone |
| `data/seed_memories.jsonl` | Nonpersonal, deliberately public project facts |
| `echo_memory/` | Local SQLite memory prototype |
| `tests/` | Restart, correction, deletion and privacy tests |
| `echo_memory/agent.py` | Python interface for an independently hosted agent |
| `journal/002-2026-11-10.md` | Conflict handling, evidence links and restart verification |

## 独立 Agent 使用示例

从仓库根目录运行 Python。适配器为每个授权范围配置独立、固定的数据库路径，只传入经同意的摘要。下面的两条记录均为合成示例。

```python
from echo_memory.agent import AgentMemory

memory = AgentMemory("data/demo.sqlite")
event = memory.remember("experience", "演练中发现配置缺项", origin="synthetic-test")
lesson = memory.reflect(event, "演练开始前检查配置", origin="manual-review")
print(memory.recall("演练", limit=2, max_chars=240))
```

单值事实可传入稳定的 `claim_key`，例如 `reply.language`。同键异值写入会抛出 `store.MemoryConflict`，其中的 `memory_id` 指向现行记录。核实后再用 `supersedes=memory_id` 写入修订。CLI 支持 `add --claim-key` 和 `add --experience-id`。所有记忆操作均在本机完成。

## Boundaries

No credentials, QQ chat logs, private conversations, API responses, personal profiles, or local memory databases belong in this public repository. **Public visibility does not imply consent to publish any private transcript.**

The prototype only stores text explicitly passed to its commands; it does not autonomously collect messages or infer intimate facts. It is a tool for experimenting with memory, not evidence of subjectivity or sentience.

No open-source license has been selected yet. Repository visibility does not itself grant reuse rights.
