# Project ECHO

> An experiment in persistent memory, self-reflection, and accountable autonomous learning.

**Status:** ECHO-005 — offline experiments on memory retention and context-sensitive expression (2026-11-11).

ECHO-005 在既有关系与时间调节状态上比较三种表达策略。[表达接口与实验报告](experiments/echo005/REPORT.md) · [成长日志 007](journal/007-2026-11-11.md)

ECHO-004.1 的本地语义检索、选择性遗忘及增量整理可在合成库复现。[实验、接口与回滚](experiments/echo0041/REPORT.md) · [成长日志 006](journal/006-2026-11-11.md)

ECHO-004 在独立 SQLite 快照上比较固定全文检索、多维索引和经反馈调整的检索路线。[认知蓝图](docs/COGNITIVE_ARCHITECTURE_V1.md) · [Atlas 与 Navigator 设计](docs/ECHO-004-design.md) · [实验与复现](experiments/echo004/REPORT.md)

ECHO-003 在旧证据被推翻时重算关系状态，并暂时停用依赖它的经验建议。独立的 `echo_reflect` 模块支持每日、事件和周期反思。[技术设计](docs/ECHO-003-design.md) · [合成实验与复现命令](experiments/echo003/REPORT.md) · [成长日志 004](journal/004-2026-11-10.md)

Project ECHO investigates whether an AI-based agent can gradually improve its behavior through **traceable experience**, rather than a fixed fictional personality. Initial research directions:

- Long-term memory that survives process restarts.
- Learning and corrections without silently rewriting history.
- Offline tests of computational affect, time regulation, and bounded language choices.
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

## ECHO-001 离线可靠性实验

在独立测试库上用 `AgentMemory("data/demo.sqlite", use_fts=True)` 启用 FTS5 trigram。
首次启用会在该文件内建立索引。普通构造方式继续使用原检索。调用
`recall("雨量计 毫米", max_tokens=60)` 可限制估算上下文用量。
`remember` 和 `reflect` 支持可选的 `source_ref` 和 `confidence`，
`reflections_for(experience_id)` 返回该经验所支持的现行反思。
从包含 Phase 01 历史的仓库根目录运行 `python experiments/echo001/run.py` 即可复测。
[技术对比](docs/ECHO-001-research.md) · [实验结果与局限](experiments/echo001/REPORT.md)

## ECHO-002 经验驱动关系模型

用固定的 120 条合成经验观察 10 位虚构人物的熟悉度、亲近度、领域可信度和模拟策略。
在仓库根目录运行 `python -m experiments.echo002.run --output experiments/echo002/results` 可复现配对实验。
[设计与可调参数](docs/ECHO-002-design.md) · [实验报告与曲线](experiments/echo002/REPORT.md) · [成长日志 003](journal/003-2026-11-10.md)

## Boundaries

No credentials, QQ chat logs, private conversations, API responses, personal profiles, or local memory databases belong in this public repository. **Public visibility does not imply consent to publish any private transcript.**

The prototype only stores text explicitly passed to its commands; it does not autonomously collect messages or infer intimate facts. It is a tool for experimenting with memory, not evidence of subjectivity or sentience.

No open-source license has been selected yet. Repository visibility does not itself grant reuse rights.
