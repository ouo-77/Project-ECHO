# ECHO-001 · 开源记忆系统技术对比

2026-11-10。下列链接固定到实际阅读的提交。研究范围为各项目相关源码、作者文档、根许可证及直接依赖清单。性能实验只比较 ECHO 自身的两个版本。

## Mem0

写入路径按 user、agent、run 筛选已有记忆。推断模式先向量召回，再调用模型抽取新事实，之后用哈希去重并记入变更历史。关闭推断的分支仍然计算嵌入。相同文本去重可以抑制重复证据，对两条措辞不同的矛盾事实还需要显式裁决。所读写入路径提供经验抽取，未验证自主反思质量。依赖清单包含 Qdrant 客户端、Pydantic 和 OpenAI SDK。ECHO 保留按用户隔离和去重，使用稳定事实键触发人工或调用方修正。 [写入路径](https://github.com/mem0ai/mem0/blob/f1791daa86d469249c9b002f62644d58a1eb96a0/mem0/memory/main.py#L881) · [官方写入文档](https://github.com/mem0ai/mem0/blob/f1791daa86d469249c9b002f62644d58a1eb96a0/docs/core-concepts/memory-operations/add.mdx) · [依赖](https://github.com/mem0ai/mem0/blob/f1791daa86d469249c9b002f62644d58a1eb96a0/pyproject.toml) · [Apache-2.0](https://github.com/mem0ai/mem0/blob/f1791daa86d469249c9b002f62644d58a1eb96a0/LICENSE)

## Letta Code

监听器按 Agent 标签和后端配置同步 Git 记忆目录。常驻记忆保留简短原则，详细材料可延后读取。维护指引要求修订时记录证据位置，专用子代理可以修复记忆仓库的合并冲突。Git 能追踪修改，但解决文件合并冲突本身不能判定哪条事实正确。依赖包括 Letta 客户端、Agent SDK、MCP SDK 和终端 UI。ECHO 采用可回查的来源指针与修订记录，数据库内即可审计。 [按 Agent 同步源码](https://github.com/letta-ai/letta-code/blob/0aed9a613f647debd881d0b033ecfc5c1450e69a/src/websocket/listener/memfs-sync.ts) · [官方维护指引](https://github.com/letta-ai/letta-code/blob/0aed9a613f647debd881d0b033ecfc5c1450e69a/src/skills/builtin/maintaining-core-memory/SKILL.md) · [冲突修复职责](https://github.com/letta-ai/letta-code/blob/0aed9a613f647debd881d0b033ecfc5c1450e69a/src/agent/subagents/builtin/memory.md) · [依赖](https://github.com/letta-ai/letta-code/blob/0aed9a613f647debd881d0b033ecfc5c1450e69a/package.json) · [Apache-2.0](https://github.com/letta-ai/letta-code/blob/0aed9a613f647debd881d0b033ecfc5c1450e69a/LICENSE)

## Graphiti

一次输入形成带参考时间的 episode，节点及事实边能追溯到输入事件。写入流程抽取、消歧、比对候选边，并可记录哪条新边使旧边失效。由此可以表达事实随时间演变。所读路径的产物是图上的知识更新，自主反思质量没有在本研究中测量。清单包含 Neo4j 驱动、NumPy 和 OpenAI SDK。对目前的单值事实，ECHO 的版本链足以保存替代关系，暂缓图数据库和双时间查询。 [episode 写入](https://github.com/getzep/graphiti/blob/e911422602068a0dc28e72a1961698269e9aefb2/graphiti_core/graphiti.py#L1334) · [边解析与失效](https://github.com/getzep/graphiti/blob/e911422602068a0dc28e72a1961698269e9aefb2/graphiti_core/utils/maintenance/edge_operations.py#L424) · [依赖](https://github.com/getzep/graphiti/blob/e911422602068a0dc28e72a1961698269e9aefb2/pyproject.toml) · [Apache-2.0](https://github.com/getzep/graphiti/blob/e911422602068a0dc28e72a1961698269e9aefb2/LICENSE) · [官方事件文档](https://help.getzep.com/graphiti/core-concepts/adding-episodes)

## Generative Agents

检索分别计算近期性、重要性与向量相关性。反思在重要性累计触发后检索记忆，让模型生成洞见，并将支持它的节点 ID 写进 thought。该路径支持经验影响后续规划，没有为同一事实提供唯一现行版本的保证。项目依赖清单固定了 Django 2.2、NumPy 1.25.2 和 OpenAI 0.27.0。ECHO 借鉴证据 ID，允许一条经验产生多条可回查的反思，反思文本仍由调用方提交。 [检索评分](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/persona/cognitive_modules/retrieve.py#L132) · [反思与证据](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/persona/cognitive_modules/reflect.py#L38) · [依赖](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/requirements.txt) · [Apache-2.0](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/LICENSE) · [作者论文](https://arxiv.org/abs/2304.03442)

## OpenClaw

该版本的默认 Memory Core 将日记、整理后的核心记忆与审核材料分层，用 SQLite 建索引。官方架构说明描述了带来源门槛的后台整理。混合检索代码合并文本与向量结果，并支持时间衰减。来源策略限制哪些记录有资格进入核心记忆，来源相同的复述不应被当作独立佐证。根包包含 MCP、模型供应商 SDK 等依赖。ECHO 借鉴可定位来源与有限上下文，保留独立 Python 接口。研究仅针对所列上游快照。 [混合检索](https://github.com/openclaw/openclaw/blob/5a7aaeb256a44d7c9b2e021a4ef6095d5f7b6e69/extensions/memory-core/src/memory/hybrid.ts#L81) · [架构文档](https://github.com/openclaw/openclaw/blob/5a7aaeb256a44d7c9b2e021a4ef6095d5f7b6e69/docs/concepts/memory-architecture.md) · [来源策略](https://github.com/openclaw/openclaw/blob/5a7aaeb256a44d7c9b2e021a4ef6095d5f7b6e69/docs/concepts/memory-provenance.md) · [依赖](https://github.com/openclaw/openclaw/blob/5a7aaeb256a44d7c9b2e021a4ef6095d5f7b6e69/package.json) · [MIT](https://github.com/openclaw/openclaw/blob/5a7aaeb256a44d7c9b2e021a4ef6095d5f7b6e69/LICENSE)

## ECHO 的差距与本次取舍

| Phase 01 的具体限制 | 本次改动 | 仍需验证 |
| --- | --- | --- |
| 连续子串无法匹配位置分散的术语 | 显式启用 FTS5 trigram，空格分词后取交集，三字符以下走字面匹配 | 同义词、分词质量和大库写入成本 |
| origin 只能表示来源类别 | source_ref 可指向具体来源记录，origin 与时间戳继续保留 | 外部来源真实性由写入方核实 |
| 缺少表达来源可信程度的字段 | importance 保留排序含义，confidence 可独立填写 0–1，缺省表示未知。修订可另报分数 | 置信度尚未校准，没有自动证据加权 |
| 反思可查经验，但经验缺少反向查询 | 新增 reflections_for，通过经验 ID 查现行反思。修订反思默认继承其证据 | 每条反思当前只关联一条经验 |
| 字符数难以表示上下文开销 | 可选估算 Token 上限，计算 ID、正文、空格和换行 | 模型词表实测及正文被截断后的答案完整度 |
| 同键事实已能报冲突，模糊表述无法自动裁决 | 保留已验证的事务与修订链，让新版本单独记录来源和置信度 | 无键、跨键或隐含矛盾 |

原型继续使用 Python 标准库和 SQLite。FTS 需要支持 trigram 的 SQLite 构建，仅在显式启用的数据库建索引，首次启用会回填已有行，之后随写入事务维护。普通入口继续使用字面检索。没有把上游源码、模型权重、素材、依赖包或上游提示词纳入发行文件。根许可证不覆盖所有子目录的独立声明，例如 Mem0 的集成插件与 OpenClaw 的第三方资源各有许可文件。本次没有分发这些组件，也没有做传递依赖的完整许可证审计。[SQLite FTS5 规范](https://sqlite.org/fts5.html#the_trigram_tokenizer)

复核标准为同一组合成查询上的准确率、错误召回、耗时与上下文用量。[ECHO-001 实验报告](../experiments/echo001/REPORT.md)
