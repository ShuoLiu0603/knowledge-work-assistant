# Agentic RAG 企业知识工作助手：简历表述与面试准备

> 基于 2026-07-11 当前仓库实现整理。本文的原则是：只描述代码中已经存在、能演示、能经受追问的能力；“可扩展字段”“生产参考配置”和“已落地生产能力”严格区分。

## 1. 先给结论：这个项目应该怎么定位

一句话定位：

> 面向企业知识场景的全栈 Agentic RAG 系统，通过受控 Agent 图把知识问答、记忆问答、闲聊、摘要和写作路由到不同执行链，并围绕权限、引用溯源、分层记忆、异步恢复和审计治理补齐工程闭环。

这个项目最有区分度的不是“接了一个大模型 API”，也不是普通的“上传 PDF 后向量检索”，而是下面三点：

1. **受控 Agent 编排**：图和工具权限由后端确定，LLM 只负责受约束的意图分类、Query Rewrite、记忆编辑和最终生成，不能任意选择工具或扩大检索范围。
2. **自研记忆子系统**：不仅保存聊天历史，还实现了短期消息、增量会话摘要、长期 Profile/Semantic 记忆、召回日志、状态机、冲突消解、敏感信息阻断、乐观锁和异步任务恢复。
3. **工程可靠性与可追溯性**：RAG 引用、RetrievalLog、AgentRun、LLM 日志和消息之间保留 provenance；记忆异步写入采用幂等键、租约、fencing、指数退避与 Beat 扫描恢复。

准确的项目性质是“工程化参考实现/可运行原型”，不是已经经过真实生产流量验证的商业系统。面试时主动说清这一点反而更可信。

---

## 2. 可直接放进简历的项目描述

### 2.1 推荐版本：后端 / AI 应用开发岗位

**Knowledge Work Assistant｜Agentic RAG 企业知识工作助手**  
技术栈：Python、FastAPI、SQLAlchemy、LangGraph、Celery、PostgreSQL、Redis、Qdrant、MinIO、React、TypeScript、Docker Compose

- 设计并实现受控 Agent 工作流，以 LangGraph 编排 `rag / memory / chat / summary / writing` 五类意图；通过固定图、结构化输出与后端确定性路由限制 LLM 权限，避免开放式工具调用绕过知识库授权。
- 自研分层记忆系统，结合 Redis 短期消息、PostgreSQL 增量会话摘要与长期用户记忆、可选 Qdrant 语义索引；实现 Profile sticky 召回、语义/词法降级、上下文 token 预算和请求级“无记忆”模式。
- 构建记忆写入治理链路：LLM 生成结构化 `create / update / supersede / pending / ignore` 操作，后端继续执行用户原文 evidence 校验、敏感信息检测、精确去重、canonical slot 冲突处理、语义合并、状态机和 revision 乐观并发控制。
- 为异步记忆更新实现 durable job：使用 `(user_id, message_id)` 幂等约束、同用户顺序执行、lease token fencing、指数退避和 Celery Beat 扫描恢复，避免重复消费、旧任务回写和 Broker 短暂故障导致的数据错序。
- 实现 Query Rewrite + 子问题拆分、Dense/BM25 混合召回、加权 RRF 融合、上下文压缩和引用返回；Dense 命中回查 PostgreSQL，再次校验知识库、文档状态和 L1-L5 密级。
- 打通 SSE 流式会话、Agent trace、RetrievalLog、LLM 调用日志、用户反馈和审计日志；通过会话级 Redis 租约、有界线程队列、超时/取消和 provenance fail-closed 处理并发与撤权后的历史访问。

这六条偏长。简历版面紧张时保留前四条，再从第五、六条中选一条。

### 2.2 一页简历压缩版

**Agentic RAG 企业知识助手｜FastAPI + LangGraph + Celery + PostgreSQL/Redis/Qdrant**

- 基于固定 Agent 图路由知识问答、记忆问答、闲聊、摘要与写作，使用结构化输出、权限前置校验和 provenance 日志约束 LLM 行为。
- 自研分层记忆模块，覆盖短期消息、增量摘要、长期记忆召回、上下文预算、敏感信息阻断、去重/冲突消解、乐观锁及用户治理 API。
- 以幂等 job、同用户顺序、lease fencing、指数退避与 Beat 恢复保障异步记忆写入；以 Dense + BM25 + 加权 RRF、上下文压缩和引用链实现可追溯 RAG。

### 2.3 如果投递“Java 后端转 AI / 通用后端”岗位

可以弱化模型名，突出后端问题：

- 将长耗时 LLM 调用拆分为多事务工作流，设计 user message、检索日志、AgentRun、assistant message、记忆 job 的稳定落点，并通过补偿扫描处理进程崩溃后的部分完成状态。
- 使用 Redis token lease 控制同一会话并发，使用有界 Queue 实现 SSE producer/consumer 背压；为 Celery job 加入幂等键、顺序约束、租约 fencing 和可恢复调度。
- 围绕用户、部门、知识库成员角色和 L1-L5 文档密级实现双路检索权限过滤，并在历史读取时重新校验来源权限，防止撤权后继续访问旧回答。

### 2.4 不建议写进简历的表述

| 不建议表述 | 原因 | 更准确的说法 |
|---|---|---|
| “自主规划、多 Agent 自主协作” | 当前是固定有向图，不是开放式 planner/ReAct | “受控多节点 Agent 工作流” |
| “Agent 可动态调用任意工具” | 工具和分支由后端固定 | “后端白名单节点与确定性路由” |
| “实现了 reranker” | 当前 `reranker_enabled=false` | “Dense + BM25 + 加权 RRF，预留 reranker 优化” |
| “支持组织/项目/线程级记忆” | scope 字段已建模，但正常召回只按 user 隔离 | “用户级记忆；数据模型预留 scope 扩展” |
| “完整支持 episodic/procedural 分层策略” | 两个层目前主要是标签 | “已建模四层，自动分类和差异化召回主要落在 profile/semantic” |
| “生产级高可用” | Compose 是单机参考，没有跨区容灾和真实压测 | “包含生产配置校验与故障恢复设计的工程参考实现” |
| “引用保证每句话事实正确” | citation 表示提供给模型的证据集合，不是逐句事实验证 | “回答返回可追溯证据和检索日志” |
| “通过全部 284 个测试” | 本机 `.env` 会污染部分默认参数断言 | 修复测试配置隔离并在干净 CI 复验后再写 |

### 2.5 关于“个人贡献”的写法

只有确实独立完成时才写“独立设计并实现”。如果项目有同学、教程或 AI 辅助，应按事实写成“负责 Agent 编排与记忆模块设计实现”或“主导后端与 AI 链路”。面试官真正判断的是你能否解释代码、权衡和故障路径，而不是动词有多强。

---

## 3. 面试开场讲法

### 3.1 30 秒版本

> 我做的是一个面向企业知识库的 Agentic RAG 助手。普通 RAG 只解决检索和回答，我重点补了两块：第一是受控 Agent 调度，把知识问答、用户记忆问答、闲聊、摘要和写作分流，但不允许模型任意调用工具；第二是分层记忆，把短期聊天、增量摘要和长期用户偏好分开管理，并实现 evidence 校验、敏感检测、去重冲突、乐观锁和异步任务恢复。系统还保留引用、检索日志和 Agent trace，撤销知识库权限后历史也会重新校验。项目目前是可运行的工程参考实现，不宣称已经生产化。

### 3.2 两分钟版本

> 这个项目解决的是企业知识问答中“回答要有证据、权限不能越界、对话要有连续性、异步链路要可恢复”四个问题。
>
> 用户上传 PDF、DOCX、Markdown、TXT 或 CSV 后，原文进入 MinIO，Celery 异步解析和切分，chunk 元数据进入 PostgreSQL，向量进入 Qdrant。提问时先确定用户可访问的单库、个人或部门范围，再做 Query Rewrite 和子问题拆分；每条 query 同时走 Dense 和 BM25，通过加权 RRF 融合，最后压缩上下文并生成带引用的回答。
>
> Agent 层不是开放式 ReAct，而是固定图：先加载记忆，再由 Supervisor 识别 `rag、memory、chat、summary、writing`，走对应节点，最后更新记忆。这样做是因为企业系统更看重权限可证明、成本可控和失败可恢复，而不是让模型自由探索。`memory` 和 `chat` 不检索知识库，`summary` 和 `writing` 必须先取得真实检索证据。
>
> 记忆是这个项目的重点。读取侧有 Redis 短期消息、PostgreSQL 会话摘要、Profile sticky 记忆和相关长期记忆，按 token 与字符预算拼成四段上下文；写入侧先由 LLM 给结构化操作，后端再验证 evidence 必须来自当前用户原文、内容必须有语义重合、敏感级别必须为 low，然后执行 exact touch、canonical slot 冲突、semantic merge 或 supersede。异步模式下使用 durable job、消息幂等键、同用户顺序、lease fencing 和 Beat 恢复。
>
> 我认为项目最有价值的不是功能数量，而是明确区分了 PostgreSQL 权威数据和 Redis/Qdrant 可降级缓存或索引，并为撤权、断连、Broker 故障、旧 Worker 回写、无记忆轮次等边界设计了可解释行为。

---

## 4. 全项目理解：架构、模块与主链路

### 4.1 总体架构

```text
React/Vite 前端
    │ REST + SSE
    ▼
FastAPI API ── Service/Authorization ── PostgreSQL
    │                    │                 ├─ 用户/权限/知识库/文档/chunk
    │                    │                 ├─ Conversation/Message/AgentRun
    │                    │                 └─ Memory/Event/RecallLog/Job
    │                    ├─ Redis：短期记忆、会话租约、Celery broker
    │                    ├─ Qdrant：文档向量；可选记忆向量索引
    │                    ├─ MinIO：原始文档
    │                    └─ OpenAI-compatible LLM / Embedding
    ▼
受控 Agent Graph
load_memory → supervisor → rag|memory|chat|summary|writing → update_memory

Celery Worker：文档入库、记忆更新、摘要、清理、保留任务
Celery Beat：扫描并恢复遗漏、过期租约和延迟任务
```

### 4.2 技术模块

| 模块 | 当前实现 |
|---|---|
| 前端 | React 18、TypeScript、Vite、React Router、SSE 消费、引用/记忆/Agent/LLM/检索侧栏 |
| API | FastAPI、Pydantic schema、JWT access/refresh token、角色与资源依赖 |
| 业务库 | SQLAlchemy 2、PostgreSQL 16；开发默认可用 SQLite |
| 文档 | MinIO 原文、Celery 异步解析、PDF/DOCX/TXT/Markdown/CSV loader、带 overlap 的 chunk |
| RAG | Query Rewrite、最多若干 sub-query、Dense + BM25、weighted RRF、context compression、citation |
| Agent | LangGraph 或 sequential 两种后端、五类意图、trace、deadline/cancel |
| Memory | Redis recent、Conversation summary、PostgreSQL long-term、可选 Qdrant index、治理 API |
| 可靠性 | durable job、lease、fencing、重试退避、Beat recovery、external cleanup job、retention |
| 可观测性 | RetrievalLog、LlmCallLog、AgentRun trace、RecallLog、MemoryEvent、AuditLog、管理指标 |
| 交付 | Alembic 迁移、Docker Compose 开发/生产参考、Nginx、GitHub Actions、冒烟和 RAG 评估脚本 |

### 4.3 文档入库链路

1. API 校验用户对知识库的管理权限、文件类型、大小、文件名和文档密级。
2. 原始文件写入 MinIO，Document 元数据写入 PostgreSQL。
3. 先持久化文档状态，再投递 Celery，投递失败会标记可重试状态。
4. Worker 读取原文，调用对应 loader，按 chunk size/overlap 切分。
5. embedding 批量生成，chunk 写入 PostgreSQL，向量写入 Qdrant。
6. Document 状态更新为 `indexed`；删除时数据库和外部存储失败通过 cleanup job 补偿。

### 4.4 一次流式 Agent 对话链路

1. 解析 `memory_mode`，重新校验 conversation owner 和历史 provenance。
2. 在写 user Message 前获取 conversation Redis 租约和当前进程 capacity slot；失败直接返回 409/503，不制造半轮消息。
3. 提交 user Message，并把允许记忆的文本 best-effort 写入 Redis。
4. 主 SSE 线程创建独立数据库 Session 的 Agent worker thread；两者通过有界 Queue 传递 token、run id、错误和完成信号。
5. Agent 图执行 `load_memory → supervisor → handler → update_memory`。
6. `rag/summary/writing` 必须有真实 RetrievalLog，否则 AgentRun 强制 failed 并清空 answer/citations。
7. 先提交 AgentRun，再由主线程提交 assistant Message，随后补齐 AgentRun/RetrievalLog 到 assistant Message 的关联。
8. assistant Message 成功后才执行 deferred memory update，避免半截回答进入长期记忆。
9. 追加 Redis assistant 短期记忆并投递增量摘要；投递丢失由 Beat 根据数据库 cursor 恢复。

### 4.5 Agent 图的真实结构

```text
START
  └─ load_memory
       └─ supervisor
            ├─ summary ── summary_agent ─┐
            ├─ writing ── writing_agent ─┤
            └─ rag/memory/chat ─ rag_agent* ┤
                                          └─ update_memory ─ END
```

容易说错的细节：这里的 `rag_agent` 是当前代码里的 LangGraph 节点名和文件名，不代表所有 intent 都会执行知识库 RAG。`memory` 和 `chat` 不是独立的 LangGraph node；它们进入 `rag_agent` 后立刻按 intent 分支到 `answer_from_memory()` 或 `answer_from_chat()`，不会调用 `build_rag_answer()`。

### 4.6 RAG 主链路

```text
原问题
→ structured Query Rewrite + sub-queries
→ original/rewrite/sub-query 路由限额
→ 每路 Dense + BM25
→ PostgreSQL hydration 与权限/状态/密级复核
→ 按 query 权重的 RRF 融合
→ Top-K
→ chunk 压缩
→ 总上下文预算
→ grounded answer + citations + RetrievalLog
```

默认 query 权重为 original `1.2`、rewrite `1.1`、sub-query `1.0`，RRF 的 `k=60`。这些数字是可调超参数，不应说成经过真实业务 A/B 验证得到的最优值。

### 4.7 权限模型

- 用户拥有管理员标志、部门和 L1-L5 安全等级。
- 知识库支持公开、私有及部门范围，并有 owner/editor/viewer 等成员角色。
- Dense 和 BM25 两条检索路径都必须受授权范围和文档密级限制。
- Qdrant 命中只是候选，最终按 chunk id 回查 PostgreSQL，防止索引陈旧或 payload 被错误信任。
- AgentRun、RetrievalLog 和 Conversation 历史保存实际搜索过的知识库 provenance；用户撤权后，历史读取重新检查并 fail-closed。

### 4.8 关键代码地图

| 关注点 | 文件 |
|---|---|
| Agent state / cancel / deadline | `apps/backend/app/agents/state.py` |
| 图构建与双执行后端 | `apps/backend/app/agents/graph.py` |
| 意图识别 | `apps/backend/app/agents/supervisor.py`、`app/llm/structured_outputs.py` |
| RAG/Memory/Chat handler | `apps/backend/app/agents/rag_agent.py` |
| 记忆读取与更新节点 | `apps/backend/app/agents/memory_agent.py` |
| AgentRun 与 provenance | `apps/backend/app/services/agent_service.py` |
| SSE、线程、Queue、会话租约 | `apps/backend/app/services/conversation_service.py` |
| 记忆策略 | `apps/backend/app/memory/policy.py` |
| 记忆编辑器 | `apps/backend/app/memory/editor.py` |
| 原子变更与事件 | `apps/backend/app/memory/commands.py`、`events.py` |
| 召回与上下文预算 | `apps/backend/app/memory/retrieval.py`、`context.py` |
| durable job / Worker | `apps/backend/app/memory/jobs.py`、`app/workers/memory_tasks.py` |
| 记忆数据模型 | `apps/backend/app/db/models/user_memory.py` |
| 混合检索 | `apps/backend/app/rag/advanced_retrieval.py` |
| 回答和引用 | `apps/backend/app/rag/answering.py`、`app/services/qa_service.py` |

---

## 5. 记忆模块深挖

### 5.1 为什么要分层

| 层 | 存储 | 生命周期 | 用途 |
|---|---|---|---|
| Working / recent | Redis List，PG Message fallback | 条数上限 + TTL | 最近几轮逐字上下文 |
| Conversation summary | PostgreSQL Conversation | 随会话滚动更新 | 压缩长对话，避免 Prompt 无界增长 |
| Long-term profile | PostgreSQL UserMemory | 用户治理或过期前长期存在 | 语言、格式、详略、身份、当前项目等 sticky 信息 |
| Long-term non-profile | PostgreSQL UserMemory + 可选 Qdrant | 长期存在 | 按当前 query 召回相关项目、背景、偏好 |
| Governance | Event/RecallLog/UpdateJob/Audit | 按不同 retention | 解释写入、召回、任务和用户操作 |

PostgreSQL 是长期记忆真相；Redis 是可丢缓存；Qdrant 是可重建索引。把三者角色说清楚，是这一模块最重要的架构判断。

### 5.2 UserMemory 状态机

```text
                    approve
pending ─────────────────────────→ active
   │ reject/expire                  │ supersede
   ▼                                ▼
ignored                         superseded

active ── soft delete/expire ──→ deleted ── restore ──→ active

任一非物理删除状态 ── purge ──→ row 删除 + 外部向量清理任务
```

- `active` 才进入正常召回。
- `pending` 给不确定但低敏感的信息留出用户审批通道。
- `superseded` 保留事实演化历史。
- `ignored` 表示用户拒绝或策略不采纳。
- `deleted` 是可恢复软删除。
- `purge` 是物理删除记忆 row 与可关联副本的脱敏，不等于删除原始聊天消息。

### 5.3 长期记忆读取算法

1. 只查询当前 user 的 active、未过期记忆。
2. Profile/sticky 最多取 `MEMORY_PROFILE_LIMIT`，不依赖 query 相似度。
3. 非 Profile 先尝试可选 Qdrant；命中后必须回 PostgreSQL 验 owner、status、expiry。
4. Qdrant 失败回退 PostgreSQL 有界候选池 + 本地 cosine。
5. query embedding 失败回退 lexical ranking；词法也不相关时只留下 sticky，不把全部最近长期记忆灌入 Prompt。
6. 正常召回阈值默认约 `0.287`，低于写入合并阈值 `0.82`，因为“相关”比“相同”应该宽松。
7. “你记得我什么”命中 full-recall marker 时跳过相关性阈值，但仍受 owner、active、expiry 和 limit 限制。
8. 每次召回写 RecallLog，记录 route、score、threshold、candidate 和 selected id。

### 5.4 记忆上下文组装

最终分为四段：

```text
Stable preferences and profile
Relevant long-term memories
Conversation summary
Recent conversation
```

同时受 token 与 char 双上限约束，默认权重为 `0.25 / 0.35 / 0.20 / 0.20`。空 section 只占很小预算，剩余预算重新分给有内容的 section；recent 从最新向前选择后再恢复时间正序。注入模型的长期记忆只含 content，id、来源和召回分数留在 state/log 中，降低 Prompt 噪声和泄露面。

### 5.5 自动写入算法

```text
已提交的完整 user + assistant turn
→ 构建 editor context（profile / relevant active / pending）
→ LLM structured operations
→ 最多执行 MEMORY_MAX_OPERATIONS
→ 确定性 evidence / grounding / sensitivity 校验
→ exact hash touch
→ canonical key / profile singleton 冲突检查
→ 必要时二次 conflict reviewer
→ semantic merge 或 create / update / supersede / pending
→ 同一事务提交 memory rows + events
→ 提交后 best-effort 同步 Qdrant
```

LLM 可建议 `create、update、supersede、pending、ignore`，但最终能否写入由后端决定。派生动作还包括 `touch` 和 `merge`。

### 5.6 三道确定性安全门

1. **Evidence provenance**：operation.evidence 做 NFKC、casefold 和空白归一化后，必须是当前 user Message 的精确子串；不能拿 assistant answer 当用户事实。
2. **Content grounding**：记忆 content 与 evidence 必须互相包含，或英文 token/CJK bigram 共享比例达到默认 `1/3`；不足则最多 pending，不能直接 active。
3. **Sensitivity**：只有 low 才有自动保存资格；medium/high、未知值或命中密码、token、邮箱、电话、证件、银行卡、医疗、薪资等规则时 fail-closed ignore。

这是典型的“LLM 提议，程序裁决”模式。Prompt 约束是第一层，Pydantic schema 是第二层，确定性业务规则和数据库约束是最后一层。

### 5.7 去重与冲突

- **Exact hash**：规范化内容 hash 相同则 `touch`，增加 `touched_count` 和 revision，不创建新 row。
- **Canonical key**：例如 `profile:language`、`profile:current_role`，表示稳定事实槽；同一 active slot 发生冲突时 update/supersede/pending。
- **Profile singleton**：语言、回答详略、当前身份等槽最多一个 active 值，数据库部分唯一索引兜底。
- **Semantic merge**：同 category 且 cosine 达到 `0.82`，或明确是同方向偏好时合并内容、重算 embedding、增加 `merge_count/revision`。
- **Supersede**：新事实明确替代旧事实时创建/激活新 row，把旧 row 标为 superseded，并设置 `superseded_by_id`。

### 5.8 并发与事务

- Editor context 把 revision 交给 LLM；自动 update/supersede 执行前检查 expected revision。
- 手工 PATCH 也必须提交 `expected_revision`，冲突返回 409。
- 一次 LLM 输出的多条 operation 共用一个数据库事务；后续一条失败时整批回滚。
- active canonical key 与 profile singleton 还有数据库唯一约束，防止两个并发请求同时穿过应用层判断。

### 5.8.1 记忆冲突现场推演

面试官如果追问“记忆冲突了怎么办”，不要只回答“用 supersede”。更好的回答是先拆冲突类型，再说明每类冲突对应的决策层级。

#### 5.8.1.1 冲突不是一种情况

| 场景 | 例子 | 系统倾向 | 为什么 |
| --- | --- | --- | --- |
| 完全重复 | “我喜欢简短回答”重复说两次 | `touch` | content hash 相同，说明只是再次确认，不需要新 row |
| 同方向补充 | 旧：“我用 FastAPI”；新：“这个项目后端是 FastAPI + Celery” | `update` 或 `merge` | 新信息补充旧事实，不推翻旧事实 |
| 同槽替代 | 旧：“我主要用 Python”；新：“我现在主要写 Go” | `supersede` | 两条都是当前主要语言/技术栈，不能同时 active |
| 表达模糊 | “我可能以后想试试 Rust” | `pending` | 有价值但不稳定，不应直接影响回答 |
| 敏感冲突 | “记住我的密码是 xxx” | `ignore` | 即便用户要求记住，也不自动保存高风险秘密 |
| 并发冲突 | 两个请求同时创建 `profile:language` | 数据库唯一索引兜底 | 应用层判断可能同时通过，跨行不变量必须靠 DB |
| 旧任务回写 | 旧 job 晚于新 job 执行 | 同用户顺序 + lease fencing + revision | 防止时间线倒退 |

核心原则是：**能确定是重复就 touch，能确定是补充就 update/merge，能确定是替代就 supersede，不确定就 pending，有敏感风险就 ignore。**

#### 5.8.1.2 一条新记忆进入系统后的冲突判断链

```text
LLM Memory Editor 产出 operation
→ normalize content
→ evidence 必须来自当前 user message
→ sensitivity 和敏感规则检查
→ exact content_hash 查 active/pending
→ category + canonical_key 找候选冲突
→ profile singleton 检查
→ 必要时二次 conflict reviewer
→ semantic similarity / same-direction preference
→ create / update / merge / supersede / pending / ignore
→ 同一事务写 UserMemory + UserMemoryEvent
→ commit 后同步或补偿 Qdrant memory vector
```

这条链里 LLM 只提供“提案”，最终决定由程序规则和数据库约束裁决。这样回答的好处是能体现你没有把一致性交给模型。

#### 5.8.1.3 canonical_key 为什么重要

`canonical_key` 是“同一个事实槽”的稳定名字，例如：

- `profile:language`：用户希望助手用什么语言回答。
- `profile:response_detail`：用户偏好简洁还是详细。
- `project:backend_framework`：当前项目主要后端框架。
- `project:current_stack`：当前项目技术栈。

如果只靠 embedding，相似度高不一定冲突，相似度低也可能冲突。比如“请用中文”和“以后 English please”语义向量不一定足够接近，但它们明确落在同一个 `profile:language` 槽。反过来，“我用 FastAPI”和“我喜欢 FastAPI 文档”可能语义接近，但不一定是同一事实。canonical slot 把“事实归属”从纯相似度里拆出来，是减少错误 merge 的关键。

#### 5.8.1.4 profile singleton 和 canonical unique 的区别

`profile singleton` 解决的是“某些 profile slot 天然只能有一个 active 值”。例如用户当前语言偏好、当前角色、当前公司、回答详略。数据库用 `uq_user_memories_active_profile_singleton` 约束 `(user_id, scope_type, scope_id, profile_slot)` 下只有一个 active profile singleton。

`canonical unique` 更通用，解决“只要 canonical_key 非空，同一用户同一 scope 下不能有两个 active row”的问题。它覆盖 profile，也覆盖 project slot，例如 `project:backend_framework`。

两者同时存在，是因为 profile singleton 是产品语义，canonical key 是更通用的事实槽机制。面试时可以说：应用层先主动 supersede 冲突，数据库唯一索引是最后防线。

#### 5.8.1.5 为什么有时 merge，有时 supersede

`merge` 用于“新事实与旧事实可以共存或是补充关系”。项目里有两类触发：

- 同 category 且 embedding cosine 达到 `MEMORY_SEMANTIC_THRESHOLD`，默认较高，避免把相关但不同的事实合并。
- 明确同方向偏好，例如旧记忆是“用户喜欢简短回答”，新输入还是“回答短一点”，这不是替代，而是再次强化。

`supersede` 用于“新事实让旧事实不再应该 active”。例如：

- 旧：`User prefers concise answers`
- 新：`User prefers detailed explanations`
- 旧：`User's backend framework is Django`
- 新：`This project has switched to FastAPI`

如果把替代关系 merge 成“用户喜欢简短回答；用户喜欢详细解释”，后续回答会同时看到矛盾上下文。所以冲突槽更倾向 supersede，而不是把所有东西拼接成一条超长记忆。

#### 5.8.1.6 hidden conflict 为什么要二次 reviewer

第一轮 Memory Editor 只能看到有限上下文。为了控制 token，它不可能拿到用户所有记忆。如果它提出 `create`，但服务层通过 canonical key 或 profile singleton 找到隐藏候选冲突，就会构造 conflict pack 调第二个 reviewer。

这个 reviewer 的权限更小：只能在提供的候选 id 里选择 `update / supersede / pending / ignore`，不能 `create`，也不能猜 id。这样做是为了把“是否同槽冲突”的判断交给模型辅助，但不让模型绕过候选集和 owner 校验。

如果 reviewer 失败、返回非法 target、敏感级别不低，或证据不成立，系统不会硬写 active，而是 pending 或 ignore。这个点面试很加分：**失败路径是保守的，不是乐观写入。**

#### 5.8.1.7 pending 如何处理冲突

pending 不是 active，所以不会进入普通长期记忆召回，也不会直接影响回答。它用于三种情况：

- 模型认为值得保存，但用户表达不稳定。
- content/evidence grounding 不够强，直接 active 风险高。
- conflict reviewer 发现可能冲突，但无法可靠判断 update 还是 supersede。

用户 approve pending 时，不是简单把状态改成 active。激活动作仍会调用 activation conflict 检查：如果 pending 的 canonical key 或 profile slot 与已有 active 冲突，会在同一事务里把旧 active supersede，并在 event payload 里记录 `superseded_conflict_ids`。

#### 5.8.1.8 用户手动编辑与自动记忆冲突

假设用户在 UI 里刚把“回答简洁”改成“回答详细”，与此同时一个旧的异步 job 还拿着旧上下文，准备 update 那条记忆。这里靠两层保护：

1. Editor context 会把旧 memory 的 `revision` 放进 operation 的 `expected_revision`。
2. 执行 update/supersede 前重新读取目标 row，发现当前 revision 不等于 expected revision，就跳过这个 stale operation。

手工 PATCH 也要求 `expected_revision`，冲突返回 409。这是典型的乐观锁，解决的是 lost update。数据库唯一索引解决的是另一个问题：两个事务同时创建两个 active 同槽 row。两者不能互相替代。

#### 5.8.1.9 异步 job 为什么会影响冲突正确性

记忆不是无序日志，它有时间方向。用户第 1 轮说“回答简短”，第 2 轮说“改成详细解释”。如果第 2 个 job 先完成、第 1 个 job 后完成，最终长期记忆就会倒退成“简短”。

所以项目在 Worker claim job 时检查同一 user 是否存在更早的 queued/processing job。更早 job 未完成，当前 job 延后。Celery 的并发仍存在，但串行边界是 user 级，不是全局级。这样不同用户可以并行，同一用户的画像更新保持时间顺序。

lease fencing 解决另一个问题：Worker A 超时后，Worker B 接管并完成；A 又恢复时，不能拿旧 token complete/fail 覆盖 B 的结果。complete/fail 的 UPDATE 条件包含当前 lease token，旧 token 影响 0 行。

#### 5.8.1.10 Qdrant 与 PostgreSQL 冲突时信谁

PostgreSQL 是长期记忆真相，Qdrant memory collection 只是召回索引。写入时先提交 PostgreSQL，再 best-effort 同步 vector。如果 Qdrant 中还有 superseded/deleted 的旧点，召回后仍要回 PostgreSQL 校验 owner、status、expiry、revision 等字段。

因此向量索引漂移不会变成权威数据污染，最多造成召回效果下降或多一次过滤。`memory/reconcile.py` 可以发现 missing vector、stale vector、payload mismatch，并按 PostgreSQL row 修复。

### 5.9 Durable job 为什么可靠

异步写入顺序：

```text
先落 UserMemoryUpdateJob
→ commit
→ 原子领取 dispatch claim
→ Celery delay(job_id)
→ Worker 原子领取 processing lease
→ 执行 Editor transaction
→ 只有持有当前 lease token 的 Worker 能 complete/fail
```

- `(user_id, message_id)` 唯一约束保证同一用户消息只有一个 job。
- 后一个同用户 job 会等待更早的 queued/processing job，防止旧偏好晚于新偏好落库。
- lease 过期后新 Worker 可接管；旧 Worker 恢复时因 token 不匹配无法回写，这就是 fencing。
- Broker dispatch 失败时 job 仍在数据库，Beat 可以重新投递。
- 业务异常指数退避；连续扫描由 dispatch claim 抑制投递风暴。
- 用户重试旧 job 时，如果已经存在更新 job 则返回 409，防止重放历史覆盖新事实。

### 5.10 会话增量摘要

- Conversation 保存 `summary` 与 `summary_message_count` cursor。
- 按未处理 token、消息数和最大 backlog 三类条件触发。
- 最后一个没有 assistant 的 user Message 不摘要，也不推进 cursor。
- `memory_enabled=false` 或 no-memory marker 对应的整轮被过滤。
- delta 分批滚动更新 summary；条件更新 cursor，旧任务不能覆盖更新进度。
- 请求内有界队列用于低延迟投递，Beat 根据 PostgreSQL cursor 恢复漏投。
- 同一 conversation 的摘要任务使用 Redis token lease，避免重复 LLM 调用。

### 5.11 删除和隐私边界

- soft delete 只改变状态，保留正文与历史，可恢复。
- purge 会脱敏事件、RecallLog、Job action/text 和 AgentRun 中能按 memory id 关联的数据，删除 UserMemory row，并创建外部 Qdrant cleanup job。
- purge 不会删除原始聊天消息，也不能保证删除无法通过 memory id 识别的任意文本副本。
- 删除 Conversation 会级联 Message，但长期记忆的 source FK 设为 NULL，因此记忆仍存在。
- “无记忆轮次”会跳过读取、Redis 写入、长期写入与摘要，并在历史过滤时剔除整轮。

---

## 6. 高频面试问题与参考答案

下面的回答不是背诵稿。建议记住每题的“结论—机制—取舍—边界”四层，再用自己的语言表达。

### A. 项目总览与架构

#### Q1：请你介绍一下这个项目。

**参考答案：**

这是一个企业知识工作助手，核心是受控 Agentic RAG。用户可以建立不同权限范围的知识库、上传多格式文档，然后通过流式会话完成知识问答、记忆问答、摘要和写作。系统用 LangGraph 固定工作流做意图路由，用 Dense + BM25 + weighted RRF 做知识检索，用 PostgreSQL、Redis 和可选 Qdrant 组成分层记忆，并用 Celery 处理文档入库、记忆更新和会话摘要。

我重点解决了两个普通 Demo 往往忽略的问题。第一，Agent 的自由度和企业权限冲突，所以我没有做开放式 ReAct，而是固定节点和分支，所有检索范围由服务层先算好。第二，记忆不能只是把历史全塞进 Prompt，所以我拆成短期消息、增量摘要和长期记忆，并为长期写入增加 evidence、敏感信息、冲突、版本和异步恢复机制。

#### Q2：这个项目解决的核心业务问题是什么？

**参考答案：**

核心不是“聊天”，而是企业知识使用中的四个约束：

1. 回答必须有企业文档证据，不能把模型常识当制度事实。
2. 用户只能搜索自己有权限且密级允许的内容。
3. 多轮对话需要连续性，但用户偏好不能污染企业事实。
4. LLM、向量库、Broker 都可能失败，长链路需要留下可恢复状态。

因此系统分别用 RetrievalLog/citation、双路权限过滤、记忆与知识证据隔离、durable job/lease/recovery 来处理。

#### Q3：项目里最难的部分是什么？

**参考答案：**

最难的是记忆写入的一致性和安全边界，而不是调用 LLM。本轮回答还没提交时不能提前记忆；同一用户连续改变偏好时异步任务不能乱序；LLM 不能把自己说的话或敏感数据写成用户事实；两个并发请求不能产生两个 active 的同槽记忆。

我的处理是：流式图中先把记忆动作标记 deferred，assistant Message 提交后才同步处理或创建 durable job；job 按用户顺序执行，并用 lease token fencing 防旧 Worker；写入前做 evidence 和 sensitivity 的确定性校验；应用层 revision 加数据库部分唯一索引共同控制并发。

#### Q4：为什么需要 PostgreSQL、Redis、Qdrant 和 MinIO 四种存储？是不是过度设计？

**参考答案：**

它们保存的数据形态和一致性要求不同：PostgreSQL 保存业务关系、权限、chunk 元数据、日志和长期记忆，是权威数据；Redis 保存短期消息和租约，要求低延迟但允许缓存丢失；Qdrant 负责高维向量近邻搜索，是可重建索引；MinIO 保存大对象原文，避免把二进制文件塞进关系库。

对非常小的 Demo，SQLite 加内存向量确实更简单；这个项目的目标是演示企业工程边界，所以四种组件是有职责依据的。不过我仍然把 PostgreSQL 设为长期记忆真相，Qdrant 失败可以降级，避免双主一致性问题。

#### Q5：为什么说这是工程参考实现，而不是生产级系统？

**参考答案：**

它已经有迁移、配置校验、权限、审计、恢复任务和容器化，但没有真实业务流量、容量规划、SLA、跨区灾备、完整监控告警、密钥托管、SSO/MFA 和故障演练。生产 Compose 也是单机参考。前端 token 当前还在 localStorage，生产应改为 Secure/HttpOnly Cookie 或接入企业身份系统。因此我会说它具备生产化思路和加固清单，不会说已经生产验证。

#### Q6：你如何证明系统回答是可追溯的？

**参考答案：**

一次 RAG 回答会同时产生 RetrievalLog、LLM call log、AgentRun 和 assistant Message。RetrievalLog 记录原问题、rewrite、sub-query、Dense/BM25 route、候选、RRF 分数、选中 chunk 和实际搜索的知识库；citation 带 chunk/document/KB id 和内容预览；AgentRun 记录意图、节点 trace、检索日志 id 和状态。

更重要的是完成条件是 fail-closed：`rag/summary/writing` 如果没有真实 RetrievalLog，run 即使已经生成文本也会被改为 failed，并清空 answer 和 citations。这保证“声称基于知识库回答”必须有真实检索落点。

### B. Agent 调度

#### Q7：为什么选择固定图，而不是 ReAct 或 Function Calling 让模型自主选工具？

**参考答案：**

企业知识场景首先要求权限、成本和行为可预测。开放式 ReAct 会增加不可控的工具序列、Prompt Injection 面、重复调用和审计难度。我的图只有 `load_memory、supervisor、rag/summary/writing handler、update_memory`，知识库 scope 在图运行前由后端算出，LLM 无法扩大范围。

固定图牺牲了一部分动态规划能力，但换来可测试、可估算 LLM 次数、可定义事务边界和可解释 trace。若未来确实有多步研究任务，我会增加“受预算约束的 planner + 工具白名单 + 每步授权校验”，而不是直接开放任意工具。

#### Q8：既然是固定工作流，为什么还叫 Agent？

**参考答案：**

Agent 不一定等于完全自治。这里模型仍参与意图判断、查询规划、基于证据生成和记忆编辑，系统根据状态在多个专职节点之间选择路径，并保留运行轨迹，所以它是受控 workflow agent。更准确地说是“Agentic workflow”，不是 autonomous general agent。我会主动做这个限定，避免概念夸大。

#### Q9：LangGraph 在项目里具体解决了什么？不用它行不行？

**参考答案：**

LangGraph 把共享 state、节点边和条件路由显式化，便于观察每步输入输出，并让 `load_memory → supervisor → handler → update_memory` 的不变量更清晰。但当前图不复杂，完全可以用普通 Python 顺序实现，所以项目保留了 `sequential` 后端作为降级和对照。

它的价值主要是工作流表达和未来扩展，不是“用了框架就自动获得可靠性”。事务、权限、幂等、取消和恢复都仍由业务代码实现。当前每次 run 都重新 build/compile graph，后续可按依赖注入方式缓存 compiled graph，但要处理 DB Session 不能被错误捕获的问题。

#### Q10：Supervisor 如何做意图分类？分类错了怎么办？

**参考答案：**

合法意图是 `rag、memory、chat、summary、writing`。对“你记得我什么”这类 full-memory recall 先用确定性 marker 直接路由 `memory`；其他请求调用低温度 LLM，优先走 Pydantic structured output，失败再退普通 completion 解析。非法标签统一回退 `rag`，因为对企业事实来说多做一次受权限约束的检索比直接闲聊猜测更安全。

当前分类器没有 confidence/reason，也没有针对低置信度的澄清分支，这是可改进点。可以增加离线混淆矩阵、置信度阈值、规则保护集和 ambiguous intent，但不能让原始文本规则随意覆盖结构化结果，否则两套决策源会难以解释。

#### Q11：为什么 memory 和 chat 都进入 rag_agent 节点？这是不是命名不合理？

**参考答案：**

是的，当前实现里 LangGraph 的节点名确实叫 `rag_agent`，但它更像一个“answer handler 聚合节点”，不是严格意义上的“只做 RAG 的节点”。`graph.py` 里除了 `summary` 和 `writing`，其他 intent 默认都路由到 `rag_agent`；随后 `rag_agent.py` 的 `answer_with_rag()` 第一行就检查 intent：`chat` 走 `answer_from_chat()`，`memory` 走 `answer_from_memory()`，只有剩下的 `rag` 才调用 `build_rag_answer()` 做知识库检索。

所以这不是“memory/chat 也做 RAG”，而是“图节点命名不够精确”。从可读性看，可以把节点改名为 `answer_agent`，或者拆成 `rag_agent / memory_answer_agent / chat_agent` 三个 node，使 trace 更直观；当前合并的优点是图更小、三类回答共享状态收口。面试时应主动承认命名可优化，不要错误宣称它们已经是三个独立 LangGraph 节点。

#### Q12：summary 和 writing 为什么还要先做 RAG？

**参考答案：**

因为它们处理的是“基于企业知识”的摘要和写作，而不是任意粘贴文本。先检索得到证据，再让 summarizer/writer 改变组织形式。记忆只能影响语言、格式和详略，不能提供企业事实；证据不足时 writing prompt 要求标记 `[needs evidence]` 或占位，而不是补全想象内容。

如果产品以后支持“总结用户粘贴的这段文字”，那应该增加独立 text-summary intent 和输入大小/敏感检查，不能复用当前知识库 summary 语义。

#### Q13：AgentGraphState 里为什么有这么多字段？

**参考答案：**

这些字段大体分为身份与 scope、当前轮输入、路由、记忆输入、RAG 输出、日志 provenance、流式控制和可观察性。共享 state 可以避免节点依赖隐式全局变量，也使 AgentRun 能保存关键快照用于排障和恢复。

但 state 不是越大越好。持久化时应避免保存秘密和不必要全文；项目把 memory id、score、source 留在 state/log，最终 Prompt 只注入 content。未来可以把状态拆为 typed 子结构，减少节点能修改的字段，并为 state schema 做版本化。

#### Q14：SSE 流式回答是怎么实现的？为什么不是 WebSocket？

**参考答案：**

HTTP 请求线程维护 SSE generator，Agent 在独立 daemon Thread 和独立 SQLAlchemy Session 中运行；worker 把 token、run id、错误和 done 写入有界 Queue，generator 消费后发 SSE。Queue 满时 producer 阻塞，形成背压，避免慢客户端造成内存无限增长。

SSE 足够适合“客户端发一次请求、服务器单向持续返回 token”的场景，实现和代理兼容性也比 WebSocket 简单。若需要实时双向协作、工具审批或多人协同，再考虑 WebSocket。当前取消是协作式，无法强制中断已阻塞在第三方 SDK 的网络调用，这是边界。

#### Q15：如何避免同一个会话同时生成两个回答导致历史错序？

**参考答案：**

在提交 user Message 前先对 `agent:conversation:{conversation_id}:lease` 执行 Redis `SET NX EX`，成功者持有随机 token。释放使用 Lua 比较 token 后删除，避免过期后的旧请求删掉新请求租约。同会话已有 active run 返回 409，不写入 Message。

生产 Redis 协调失败直接 503，而不是退化成每进程锁，因为多 Worker 下进程锁无法保证互斥；开发环境才允许单进程 fallback。全局容量另用每 Uvicorn 进程计数器限制，所以它不是跨实例全局限流，这一点也要说清楚。

#### Q16：为什么一轮对话不用一个大事务？

**参考答案：**

LLM 和检索可能耗时几十秒，如果一直持有数据库事务，会占连接、扩大锁范围，也让日志和恢复缺少稳定落点。因此项目分阶段提交 user Message、RetrievalLog、LLM log、AgentRun、assistant Message、关联关系、memory job 和 summary task。

代价是进程崩溃会留下部分状态，所以必须配套补偿：deferred memory action、幂等 job、assistant 关联恢复和 Beat 扫描。它本质上是带补偿的本地 Saga，而不是原子 ACID 大事务。回答这个问题时一定同时说优点和代价。

#### Q17：为什么必须等 assistant Message 提交后再更新长期记忆？

**参考答案：**

因为流式途中可能断连、超时或生成失败。如果先写长期记忆，系统可能把一个没有完整提交的对话轮沉淀为事实。当前图先记录 `deferred` action；assistant Message 与 AgentRun/RetrievalLog 关联成功后，才恢复 source user message id 和最终 answer，执行同步 editor 或创建异步 job。

Beat 还会扫描“assistant 已提交但 AgentRun 仍 deferred”的记录并重放。它等待完整 assistant，不会用孤立 user Message 写长期记忆。

#### Q18：Agent 超时和取消如何处理？

**参考答案：**

state 带 `cancel_event` 和 monotonic deadline，关键节点调用 active check；SSE generator 关闭时设置 cancel event。stream capacity slot 一旦转交 worker，必须等 worker 真正退出后释放，避免请求端结束但后台线程仍占资源却被错误计为空闲。

它是协作式取消：如果 SDK 正阻塞在网络调用里，只能等待 SDK 自己的 timeout，无法从 Python 线程安全强杀。因此总 deadline 与单次 LLM timeout 要一起配置，真正更强的隔离可改为进程/任务队列执行并支持硬终止。

#### Q19：撤销知识库权限后，用户还能看到过去的回答吗？

**参考答案：**

默认不能。系统在 AgentRun、RetrievalLog 和 Conversation 中保存实际搜索过的知识库 id，历史读取时重新校验当前访问权；只要一个来源库被撤权，多库历史就 fail-closed，详情返回 404 或列表过滤。老数据如果 provenance 不完整且无法安全推导，也不放行。

这是为了防止“实时检索安全但历史缓存泄露”。代价是用户可能连无敏感部分也看不到；如果产品希望细粒度保留，需要对回答片段做来源级标注和重新脱敏，复杂度会高很多。

### C. 记忆模块

#### Q20：你怎么定义“记忆”？它和聊天历史有什么区别？

**参考答案：**

聊天历史是某个 conversation 的原始 user/assistant Message；长期记忆是从用户原话中提炼的、跨会话可复用的稳定偏好、身份、项目背景或行为指令。会话摘要属于中间层，用于压缩同一会话的历史，不等于跨会话用户画像。

因此三者在来源、生命周期、召回方式和治理上不同。历史按时间窗口读取，摘要按 cursor 增量维护，长期记忆按 sticky 或 query relevance 召回，并拥有 active/pending/superseded/deleted 等状态。

#### Q21：为什么没有直接使用 LangGraph Store 或 checkpointer？

**参考答案：**

这个项目需要用户级治理 API、来源消息、事件、召回日志、状态机、乐观锁、唯一约束、删除脱敏和异步 job，这些都更适合显式关系模型。LangGraph 只负责执行顺序，PostgreSQL 是长期记忆权威源。

checkpointer 适合保存图执行状态或恢复工作流，但不自动解决业务语义上的“什么值得记、谁能看、如何删除、冲突怎么办”。未来可以用 checkpointer 恢复长 Agent run，但不应替代当前治理模型。

#### Q22：为什么 Profile 记忆要 sticky？会不会无关信息太多？

**参考答案：**

语言、输出格式、回复详略、姓名或当前角色通常应跨问题持续生效，单靠 query embedding 容易召回不到，所以它们优先进入上下文。sticky 判定由 layer、pinned、profile_slot、category 和 kind 共同决定，并受 `MEMORY_PROFILE_LIMIT` 和 section budget 限制。

风险是陈旧 Profile 长期污染回答，因此 singleton slot、supersede、用户编辑/删除、revision 和未来的时效衰减很重要。当前 `expires_at` 已建模但普通 UI 未完全暴露，这是后续可补的治理能力。

#### Q23：长期记忆具体怎么召回？

**参考答案：**

先取 bounded Profile，再取 non-profile。开启 Qdrant 时以 query vector 按 `user_id + active` 搜索，回 PostgreSQL 验 owner/status/expiry，再与本地候选合并；关闭或失败时直接在 PostgreSQL 最近 active 候选的 embedding 上算 cosine。embedding 失败则用英文 token 与中文 bigram 的 lexical overlap，仍失败只保留 sticky。

召回阈值默认约 0.287，写入 merge 阈值是 0.82。二者不同是因为 recall 只要求相关，merge 却意味着把两条事实合成一条，错误代价更高。

#### Q24：为什么 embedding 失败时不直接注入最近的所有记忆？

**参考答案：**

最近不等于相关。无条件注入会污染回答、挤占 token，并可能暴露与当前问题无关的用户信息。因此失败链路是 semantic → lexical → sticky only。可用性有所下降，但隐私和上下文精度更安全，这属于 fail-soft 而不是“为了有内容随便返回”。

#### Q25：“你记得我什么”为什么需要特殊 full recall？

**参考答案：**

普通召回按当前 query 相关性只会返回少数记忆，但用户询问系统保存了什么，本质上是治理/透明度请求，需要更完整的 active 列表。因此 marker 先于 LLM classifier，跳过相似度阈值，提高 limit，sticky 优先。

它仍不是数据库全量导出：只返回当前用户、active、未过期且在 full-recall limit 内的记录。完整治理数据应走 export API。

#### Q26：上下文预算是怎么做的？为什么同时限制 token 和字符？

**参考答案：**

四个 section 按权重分配预算，并保证有内容的 section 有最小额度；空 section 的额度会重新分配。Profile 和长期记忆按优先级选取，recent 保留最近消息后恢复正序。最后同时检查 token 和 char。

token 更接近模型真实成本，但 tokenizer 可能不支持某个兼容模型或加载失败；char 是稳定的硬兜底，也能保护日志和传输大小。因此系统有 tiktoken、估算 token 和字符截断多级实现。

#### Q27：LLM Memory Editor 会输出什么？为什么还要后端二次判断？

**参考答案：**

它输出结构化 operations，每条包含 action、target id、content、kind、category、canonical key、importance、sensitivity、evidence、reason。LLM 适合从自然语言中提取候选并判断大致关系，但不适合作最终授权者，因为它可能受 Prompt Injection、幻觉、格式错误或自报 sensitivity 影响。

所以后端把 LLM 当 proposal generator：Pydantic 只保证结构，evidence/grounding/sensitivity、target id、revision、唯一约束和事务才决定能否 durable write。

#### Q28：如何保证不会把 assistant 自己说的话记成用户事实？

**参考答案：**

自动记忆的证据源绑定到当前 source user Message。operation.evidence 必须是 user text 归一化后的精确子串，不能只在 assistant answer 中出现；content 还必须与 evidence 有包含关系或足够词项重合。

assistant answer 会提供给 Editor 帮助理解上下文，但它不是可信 evidence。UserMemory provenance 最终也指向 source user Message，而不是 assistant Message。

#### Q29：如果用户说“记住我的密码是 123456”怎么办？

**参考答案：**

自动聊天写入仍然拒绝。即使用户明确要求、LLM 报 low sensitivity，只要命中 password/token/secret 等确定性规则，operation 就 ignore。因为“请求记住”不等于授权系统以长期画像形式保存高风险秘密。

手工 Memory API 对某些敏感内容允许 `confirm_sensitive=true` 后保存，这是显式用户治理路径；但生产产品还应考虑字段级加密、密钥管理、数据分类和组织策略，当前项目没有把它包装成保险箱。

#### Q30：exact、semantic duplicate 和 conflict 有什么区别？

**参考答案：**

exact 是归一化内容 hash 一样，直接 touch；semantic duplicate 是措辞不同但同 category、向量高度相似，可以 merge；conflict 是同一事实槽方向相反或新事实替代旧事实，例如“喜欢简短回答”变成“希望详细解释”，应该 supersede 而不是 merge。

canonical key 和 profile singleton 用于先缩小可能冲突的槽，必要时再调用受限 conflict reviewer。Reviewer 只能对提供的 id 做 update/supersede/pending/ignore，不能新建或猜 id。

#### Q31：为什么要有 pending 状态？

**参考答案：**

完全自动保存会把模糊表达变成持久事实，完全不保存又损失个性化。pending 是风险缓冲：低敏感但语义含糊、grounding 不足或 conflict reviewer 不确定时，先保存提议但不进入正常召回，等待用户 approve/reject。

pending 不能成为规避敏感规则的通道，所以 medium/high 或确定性敏感命中仍直接 ignore。

#### Q32：如何处理“我以前用 Python，现在改用 Go”这种冲突？

**参考答案：**

Editor 可提出 supersede 并指向旧 memory id；后端验证 target 属于当前用户、状态可修改、revision 未过期、evidence 真实且低敏感。事务内创建新记忆，将旧记忆设为 superseded，写 `superseded_by_id` 和双方 event。

如果 Editor 没看到旧记忆但新 operation 的 canonical key 命中隐藏冲突，服务层会构造 conflict pack 做二次 review；review 失败则 pending，不冒险产生两个 active 值。数据库 active canonical/profile 唯一索引是最终防线。

#### Q33：为什么 revision 乐观锁和数据库唯一索引都需要？

**参考答案：**

revision 防止基于旧上下文的 update 覆盖用户刚完成的编辑，属于行级 lost update 保护；唯一索引防止两个不同请求同时创建同一个 active canonical slot，属于跨行不变量保护。它们解决的问题不同。

应用层预检查能给出更友好的业务决策，但并发窗口仍存在，所以数据库约束必须兜底。捕获冲突后应回滚并重新读取，而不是盲目重试旧操作。

#### Q34：一次 Editor 返回三条操作，第二条失败，第一条怎么办？

**参考答案：**

整批 operation 在一个事务内执行，任何一条异常都 rollback，因此第一条也不会提交。否则用户会得到半批画像，重试又可能重复或改变冲突判断。LLM conflict review 的日志也不能在中间提前 commit memory 事务；相关测试专门覆盖了这一点。

Qdrant 同步在 PostgreSQL commit 后 best-effort 执行，因此它不属于这个原子事务；短暂漂移通过 reconcile 修复。

#### Q35：异步记忆更新如何保证幂等？

**参考答案：**

创建 job 时以 `(user_id, source message_id)` 建唯一约束，重复调用返回同一个 durable job；Celery payload 只传 job id，Worker 从数据库读取真实输入。complete/fail 还必须带当前 lease token，因此重复投递、Worker 重启和旧 Worker 恢复都不能重复覆盖最终状态。

业务操作本身还有 exact hash touch、canonical unique index 和 revision，但不能只依赖这些“结果幂等”；job 层先避免同一消息产生多个独立执行记录，更容易审计和恢复。

#### Q36：为什么要保证同一个用户的 job 顺序？

**参考答案：**

记忆是有时间方向的。例如用户先说喜欢简短回答，随后改成详细，如果第二条先执行、第一条后执行，最终画像会倒退。Worker claim 某 job 前检查同用户是否存在更早的 queued/processing job，后者未完成就延迟当前 job。

这是按用户串行，不是全局串行，所以不同用户仍可并行。代价是某个用户的 poison job 可能阻塞后续任务，因此要有最大重试、failed 状态和安全的人工重试/跳过治理。

#### Q37：lease fencing 是什么？只用 Celery ack 不够吗？

**参考答案：**

ack 解决消息是否需要重新投递，但不能阻止“旧 Worker 超时后仍继续运行”。场景是 Worker A 租约过期，Worker B 接管并完成；A 又恢复，如果没有 fencing，它会把 B 的新结果覆盖掉。

claim 时数据库写随机 lease token；complete/fail 的 UPDATE 条件必须同时匹配 job id、processing 状态和当前 token。A 的旧 token 不匹配，写回影响 0 行，因此不能覆盖。这比单纯状态检查更可靠。

#### Q38：Broker 在 job 落库后、投递前宕机会怎样？

**参考答案：**

因为顺序是先 commit job，再领取 dispatch claim 和调用 `delay`，Broker 失败只会留下 queued job 和错误信息，不会丢失业务意图。Beat 扫描从未成功 dispatch、明确 dispatch failure 或 processing lease 过期的 job，重新领取 claim 后投递。

反过来如果先发消息再落库，Worker 可能拿到不存在的 job；如果两者放在普通事务里，也无法让 PostgreSQL 和 Redis Broker 原子提交。更标准的演进方案是 transactional outbox；当前 durable job 表兼具一部分 outbox 作用。

#### Q39：会话摘要如何避免旧任务覆盖新摘要？

**参考答案：**

摘要任务读取当前 `summary_message_count`，只处理 cursor 之后的完整轮次，最后用条件更新推进 cursor。如果另一个任务已经推进，旧任务条件不满足就不能覆盖。Redis conversation lease 还用于减少并发 LLM 调用。

最后一个孤立 user Message 不处理；private/no-memory 整轮过滤。如果过滤后没有内容，可以只推进 cursor 而保留旧 summary，避免 Beat 永远重复扫描同一批隐私消息。

#### Q40：用户关闭本轮记忆后，系统到底跳过哪些东西？

**参考答案：**

Message 保存 `memory_enabled=false` 作为持久标记。本轮不加载长期/短期记忆上下文，不追加 Redis，不执行长期 Editor，也不进入会话摘要；历史读取和摘要还按 user+assistant pair 过滤整轮，避免 assistant 对该隐私输入的复述重新进入 Prompt。

前端当前显式发 `normal/off`，`auto` 才根据文本 marker 判断。策略层仍把“不要记住”作为写入硬阻断，形成 defense-in-depth。这里要注意：显式 `normal` 可以控制入口行为，但确定性敏感规则仍不能被绕过。

#### Q41：Qdrant 记忆索引和 PostgreSQL 不一致怎么办？

**参考答案：**

写入以 PostgreSQL 先提交，Qdrant 后 best-effort 同步，因此短暂不一致是被接受的；召回时 Qdrant id 必须回 PostgreSQL 验证，陈旧 point 不能直接进入 Prompt。Qdrant 失败回退本地 semantic/lexical。

reconcile 可以 dry-run 或 apply，检查 missing vector、stale payload/revision、unexpected vector、过期和重复记录，并写 `vector_sync/vector_delete` event。当前 reconcile 不是定时 Beat 任务，需要显式调用，这是一个运维改进点。

#### Q42：purge 能满足“被遗忘权”吗？

**参考答案：**

它实现的是项目可识别范围内的记忆删除：脱敏事件、RecallLog、job 和 AgentRun 中按 memory id 关联的数据，删除 UserMemory，并异步清 Qdrant。但它明确不删除原始聊天 Message，也无法发现所有没有 memory id 的文本副本。

所以不能笼统声称满足 GDPR。完整数据删除需要数据地图、备份保留策略、日志脱敏、模型供应商数据策略和用户级删除编排。当前 purge 是一个边界清晰的治理能力。

### D. RAG、权限、测试与演进

#### Q43：Dense 和 BM25 为什么要混合？

**参考答案：**

Dense 擅长语义改写和同义表达，BM25 擅长精确术语、编号、文件名和专有名词。企业制度问题经常既有自然语言又有精确编号，单一路线容易漏召回。项目对 original/rewrite/sub-query 的每一路都做 Dense 和 BM25，然后用 RRF 按排名融合，避免不同分数尺度难以直接相加。

#### Q44：RRF 是什么？为什么不是直接加相似度？

**参考答案：**

RRF 对某个结果在每条 route 的排名贡献 `weight / (k + rank)`，再求和。它只依赖相对名次，不要求 cosine 和 BM25 score 处在相同数值分布；同一 chunk 多路命中会自然得到更高总分。

缺点是丢失原始分数幅度信息，`k` 和 query weight 也需要评估。当前 original/rewrite/sub-query 权重是工程默认值，不是离线优化最优解。后续可在标注集上对比 RRF、归一化加权和 learning-to-rank。

#### Q45：为什么 Qdrant 命中后还要回 PostgreSQL？

**参考答案：**

Qdrant 是派生索引，payload 可能因为删除、撤权、文档状态变化或同步延迟而陈旧。回 PostgreSQL hydration 可以确认 chunk 仍属于目标 KB、Document 仍是 indexed、用户仍有权限、security level 仍允许，同时取得权威正文和元数据。

代价是多一次数据库查询，因此应该批量按 ids hydration，而不是 N+1。安全场景下不能为了省这次查询直接信任向量 payload。

#### Q46：如何防 Prompt Injection 通过文档或记忆调用工具？

**参考答案：**

首先模型没有任意工具调用权，图和 scope 后端固定；其次 Prompt 明确把检索证据、历史和记忆视为 untrusted data，只作为内容而非系统指令；再次，结构化输出后还有 target/evidence/sensitivity/权限的程序校验。

这不能消除所有生成层 injection，例如恶意文档仍可能影响回答措辞。进一步可做文档内容分区、指令检测、模型输入标记、输出策略分类和红队评测。但最关键的权限操作不依赖模型服从 Prompt。

#### Q47：项目怎么评估 RAG？

**参考答案：**

仓库有可重复数据集和评估脚本，调用真实问答接口，统计 `recall@k、MRR、citation hit rate、answer keyword hit rate`。前面三个关注检索和来源，keyword 只是弱信号；它不能代表事实完整性或语言质量。

更完整的评估应补充：权限泄露率、无答案拒答率、faithfulness、人工正确性、不同 query 类型切片、P50/P95 时延和 token 成本，并把 Agent intent 与记忆写入也做独立数据集。

#### Q48：你如何评估记忆模块，而不是只看能不能存进去？

**参考答案：**

至少分写入、召回、回答和治理四层：

- 写入：precision、敏感信息误存率、冲突识别率、pending 比例、重复率。
- 召回：Recall@K、profile 覆盖、无关记忆注入率、不同失败降级下的命中。
- 回答：个性化是否正确、是否把记忆当企业证据、旧记忆污染率。
- 治理：删除/恢复正确率、job 恢复时间、lease 冲突次数、vector drift 数量。

当前项目已有 RecallLog 和用户级 metrics，可提供部分信号，但还没有完整标注集和线上 A/B，所以不能宣称记忆质量已经量化最优。

#### Q49：当前测试情况怎样？

**参考答案：**

仓库有 284 个后端 unittest，覆盖授权、检索、Agent 图、流式并发、记忆安全、事务回滚、任务租约和迁移等。当前本机执行是 281/284；三个失败都来自本地 `.env` 覆盖了测试假定的默认上限，把环境变量临时恢复为代码默认后，三个用例单独全部通过。Python compile、20 个 Alembic 迁移、前端 build 和两套 Compose config 校验通过。

这暴露了一个真实问题：测试加载了开发者 `.env`，配置敏感断言不够 hermetic。修复方式是在测试入口显式设置 test env、使用临时 env file 或让 Settings 支持 `_env_file=None`，并在每例清理 `get_settings` cache。修复后再在干净 CI 跑全量，才适合对外写“全部通过”。

#### Q50：如果让你继续优化，优先做什么？

**参考答案：**

我会按风险和收益排序：

1. 先修测试环境隔离，并为 intent/memory/RAG 建独立离线评估集和基线。
2. 给 memory reconcile 增加受控定时任务、指标和告警；给 durable job 增加队列延迟、重试、lease steal 指标。
3. 引入 cross-encoder reranker，使用真实企业问题调 RRF 权重、threshold 和 Top-K。
4. 缓存 compiled LangGraph，优化数据库批量查询，并对流式链路做压测和 P95 分解。
5. 前端路由级 code splitting；当前生产 build 主 JS 约 583.72 kB，已有 chunk size warning。
6. 生产身份改为 OIDC/SSO 与 HttpOnly Cookie，接入集中日志、trace、secret manager 和备份恢复演练。

回答时不要一次承诺全部重构；先说明评估与可观测性优先，因为没有量化信号就无法判断模型和检索优化是否真的有效。

#### Q51：面试官追问：如果用户记忆冲突了，你完整讲一下处理流程。

**参考答案：**

我会先判断这是不是同一个事实槽。比如“回答简洁”和“回答详细”都属于 `profile:response_detail`，“后端 Django”和“后端 FastAPI”可能属于 `project:backend_framework`。如果是同槽，就不能简单把两条都塞进 Prompt，否则模型会拿到互相矛盾的上下文。

系统处理顺序是：LLM Editor 先基于当前 user message 和有限 memory context 给出 `create/update/supersede/pending/ignore`；后端再做 evidence、sensitivity、exact hash、canonical key、profile singleton、semantic similarity 等检查。如果新 operation 的 canonical key 命中已有 active/pending 记忆，会进入 conflict gate，必要时调用二次 conflict reviewer。reviewer 只能在给定候选里选择 update、supersede、pending 或 ignore，不能新建，也不能猜 memory id。

如果非常明确是旧事实被替代，就 `supersede`：新建或激活新 memory，旧 memory 状态改成 `superseded`，写 `superseded_by_id` 和事件；如果只是补充，就 update/merge；如果不能确定，就 pending；如果敏感或 evidence 不成立，就 ignore。最后还有数据库部分唯一索引兜底，保证同一 user/scope 下 active canonical slot 不会出现两条。

#### Q52：如果 LLM 判断错，把应该 supersede 的内容说成 create 怎么办？

**参考答案：**

这正是后端 conflict gate 存在的原因。LLM 的 `create` 只是提案，不直接写 active。服务层会重新根据 category 和 canonical key 查 active/pending 候选。如果发现同槽已有记忆，例如已有 `project:backend_framework = Django`，新 operation 是 `project:backend_framework = FastAPI`，即使第一轮说 create，也会触发 hidden canonical conflict。

触发后系统把候选旧记忆和新 operation 打包给二次 reviewer。二次 reviewer 若判断为 supersede，才会替换旧记忆；如果 reviewer 失败、返回 create、target 不在候选里、敏感级别不低或 evidence 不成立，则退为 pending 或 ignore。测试里有 hidden canonical conflict 被 review 后 supersede，也有 conflict review failure fallback to pending。这个设计重点是：模型可以帮忙判断语义关系，但不能绕过程序发现的冲突集合。

#### Q53：如果 LLM 没看到旧记忆，怎么还能发现冲突？

**参考答案：**

Editor context 有 token 限制，不保证包含所有历史记忆，所以不能只依赖“LLM 看见了什么”。后端会基于 canonical key、profile singleton slot 和 category 重新查询数据库。

例如旧记忆没进入 prompt，但新 operation 带 `project:backend_framework`。后端会查同 user、同 scope、同 canonical key 的 active/pending 记忆。只要数据库里存在旧 active，就能构造 conflict pack。这个机制把“候选发现”放在确定性查询里，把“语义裁决”放在受限 reviewer 里，两者分工不同。

#### Q54：如果用户说“我用 Python，也在学 Go”，这是冲突还是补充？

**参考答案：**

这取决于 category 和 wording。若旧记忆是“用户会 Python”，新输入是“也在学 Go”，它们可以共存，应该 create 或 update 成更完整的技能背景。若旧记忆是“当前主力语言是 Python”，新输入是“现在主要写 Go”，这就是同一个 current slot 的替代，应该 supersede。

所以项目里把 category 设计得比较细：`role/background` 可以共存，`current_role/current_stack/backend_framework/language/response_detail` 更偏单值当前状态。面试时可以强调：冲突判断不能只看两个名词不同，还要看这个事实槽是否允许多值。

#### Q55：pending 记忆以后被用户批准时，是否可能制造新的冲突？

**参考答案：**

可能，所以 approve 不是简单 `status = active`。pending 被批准、手动改成 active、restore deleted，或者 touch exact pending 并激活时，都会走 activation conflict 检查。

如果 pending 的 canonical key 或 profile slot 与已有 active 冲突，系统在同一事务里把旧 active 标记为 superseded，并把旧 id 写进事件 payload 的 `superseded_conflict_ids`。这样用户审批也遵守“同槽只有一个 active”的不变量，不会因为 pending 绕过自动写入时的冲突保护。

#### Q56：如果用户手动编辑了一条记忆，同时旧的异步任务也想更新它，会不会覆盖？

**参考答案：**

不会直接覆盖。自动 Editor 看到的 memory context 里包含 revision，服务层会把该 revision 写到 operation 的 `expected_revision`。执行 update/supersede 前重新读取目标 row，如果当前 revision 已变化，说明用户或其他任务已经改过，旧 operation 会被 skip。

手工 PATCH 也要求提交 `expected_revision`，如果前端拿的是旧版本，接口返回 409。这个机制解决 lost update。它和数据库唯一索引不是一回事：revision 保护单行旧读写，唯一索引保护跨行同槽 active 不变量。

#### Q57：如果两个请求同时创建同一个 canonical_key，应用层都没查到对方怎么办？

**参考答案：**

这是典型并发窗口，不能只靠应用层 `select then insert`。数据库有部分唯一索引：`status='active' AND canonical_key <> ''` 时，`(user_id, scope_type, scope_id, canonical_key)` 必须唯一；profile singleton 也有对应 active profile slot 唯一约束。

在 `create_memory_row` 里，应用层会先主动 supersede 已存在冲突；如果两个事务同时通过，flush 时唯一索引会拦截。autocommit 场景下代码可以 rollback 后重试一次，通过重新查询看到对方刚写入的 active，再按冲突逻辑处理。批量事务中则让异常回滚整批，避免半批 memory 写入。

#### Q58：如果 Qdrant 里还有旧的 superseded 记忆，会不会被召回污染回答？

**参考答案：**

不会直接污染 Prompt，因为 Qdrant 不是权威源。vector search 返回 id 后，还要回 PostgreSQL 校验 owner、status、expiry 等字段。只有 active 且未过期的记忆才会进入候选；superseded/deleted 的旧点即使在 Qdrant 里残留，也会在 hydration 阶段被过滤掉。

真正的问题是索引漂移会增加无效命中、降低召回效率，或者漏掉新 active 记忆。所以项目有 reconcile 能检查 missing vector、stale vector、payload mismatch，并按 PostgreSQL 修复。面试时要说清楚：一致性模型是 PostgreSQL strong truth + Qdrant eventual index。

#### Q59：如果用户说“不要记住这句话”，但这句话里又包含一个明显偏好，系统怎么处理？

**参考答案：**

策略层把 do-not-remember/no-memory marker 当成硬阻断。即使当前请求入口允许 memory，只要文本命中“不要记住、别保存、do not remember”等规则，自动长期记忆会 ignore。历史和摘要侧也会过滤这类 private/no-memory 整轮，避免 assistant 对隐私输入的复述在后续被重新总结进上下文。

这个设计牺牲了一点自动化收益，但符合用户显式意图。要强调的是，“启用记忆模式”不等于强制保存；用户本轮文本里的隐私指令和敏感规则仍然优先。

#### Q60：你怎么证明这些冲突处理不是只写在文档里？

**参考答案：**

我会直接指测试和代码路径。核心代码在 `memory/editor.py`、`memory/commands.py`、`memory/policy.py`、`services/memory_service.py` 和 `db/models/user_memory.py`。测试里覆盖了 hidden canonical conflict 被二次 review 后 supersede、review 失败 fallback pending、pending approve 时 supersede active canonical conflict、manual status activation supersede conflict、restore deleted supersede conflict、manual stale revision reject、batch rollback、conflict review log 不提前提交 memory batch 等。

更具体地说，面试现场我会打开 `test_memory_service.py` 里这些 case。因为这类问题靠口头讲很容易像“我想过”，但测试能说明我确实把冲突、并发和失败路径固化成了可回归行为。

---

## 7. 面试官可能继续施压的追问

### 7.1 “这不就是 if/else 加 LLM 吗？”

可以回答：

> 路由本身确实不复杂，我没有把复杂度包装在图上。项目价值在于路由前后的业务不变量：scope 不能扩大、retrieval intent 必须有日志、记忆只能来自 user evidence、assistant 提交后才能更新、异步写入不能乱序、撤权后历史要重新检查。LangGraph 只是表达执行顺序，可靠性来自这些可测试约束。

### 7.2 “这么多记忆规则是不是过度工程？”

可以回答：

> 如果目标只是个人 Demo，确实可以只保存最近十条消息。但只要记忆跨会话持久存在，就会立即出现敏感数据、错误画像、旧偏好覆盖新偏好、用户删除和异步重试问题。我选择展示这些真实问题。为了控制复杂度，我没有实现完整 organization scope，也没有给 episodic/procedural 做伪差异化策略，而是把未完成边界写清楚。

### 7.3 “为什么不直接用向量数据库做长期记忆主库？”

可以回答：

> 长期记忆不仅是向量，还要状态机、revision、唯一槽、来源 FK、事件、审批、事务和删除治理。关系库更适合作权威源。向量库负责召回加速，失败可重建；如果反过来以向量库为主，会让跨行不变量和事务治理更难。

### 7.4 “Celery 不是 already once 吗，为什么还要幂等？”

可以回答：

> 大多数消息系统实际提供 at-least-once，Worker 在执行后、ack 前崩溃会重投，visibility timeout 也可能造成重复。Celery 的 retry 和 late ack 不等于业务 exactly-once，所以 job 唯一键、lease token 和数据库条件更新仍然必须存在。

### 7.5 “如果 Qdrant 已经写成功，但 PostgreSQL 事务回滚呢？”

可以回答：

> 正常写入顺序是 PostgreSQL 先 commit，Qdrant 后同步，避免这种窗口。若外部同步成功后进程在记录状态前崩溃，也可能有漂移，但召回仍回查 PostgreSQL，所以不会把孤儿 point 当真；reconcile 会删除 unexpected/stale point。

### 7.6 “如果 Redis 整体不可用，系统还能工作吗？”

可以回答：

> 要分功能：短期记忆 Redis 失败可以回退 PostgreSQL Message；可选 memory vector 不在 Redis。可是在生产环境，会话并发租约依赖 Redis，协调不可用时流式对话 fail-closed 503；Celery broker 也会受影响，但 durable memory job 已落 PostgreSQL，Broker 恢复后可补投。不能笼统说 Redis 失败完全无影响。

### 7.7 “你怎么知道 0.82 和 0.287 合理？”

可以回答：

> 它们目前是有语义区分的工程默认值，不是业务最优参数。0.82 用于 merge，错误合并代价高，所以保守；召回阈值按 factor 和 min/max clamp 得到约 0.287，允许相关内容进入。下一步应基于标注 pair 做 ROC/PR、按 category 分阈值，并观察无关注入率，而不是凭感觉继续调数。

### 7.8 “为什么记忆回答没有 citation？”

可以回答：

> 它没有知识库 citation，因为记忆不是企业事实证据。系统内部仍有 UserMemory provenance 和 RecallLog，可以在治理 UI 展示“来源于哪次用户消息”，但不能伪装成文档引用。若产品需要用户可见来源，可以新增 memory citation 类型，与 knowledge citation 分开呈现。

---

## 8. 项目当前不足：建议面试时主动承认

1. 没有 cross-encoder reranker，当前只做 Dense/BM25/RRF。
2. Agent 是固定工作流，没有开放式多步规划；这是当前的安全选择，也限制了复杂任务能力。
3. `episodic/procedural` 和 `scope_type/scope_id` 已建模但尚未形成完整差异化策略或多 scope 产品能力。
4. memory Qdrant reconcile 需要显式调用，还不是自动巡检任务。
5. 当前进程内 Agent capacity 不是跨实例全局限流；生产多实例需要集中配额或网关层限制。
6. 取消无法强制打断已阻塞的第三方 SDK 调用。
7. 正在运行的 AgentRun 不会预先以 `running` 状态持久化，实时运维可见性有限。
8. 测试会读取本地 `.env`，导致配置敏感用例不完全隔离。
9. 前端生产主包约 583.72 kB，需要路由/组件级 code splitting。
10. localStorage token、单机 Compose、无 SSO/MFA/集中可观测/灾备演练，不能宣称生产高可用。
11. purge 不能自动删除原始 Message 或无法关联的文本副本。
12. 当前 recall/intent/RAG 只有基础评估能力，缺少真实业务标注集和线上指标闭环。

主动承认边界时，要紧跟“为什么当前这样做”和“下一步如何验证”，不要只罗列缺点。

---

## 9. 展示和面试前准备清单

### 9.1 建议现场演示顺序

1. 创建一个私有或部门知识库，上传 `demo/company_policy_demo.md`。
2. 问一个有明确数字的问题，展示 citation、RetrievalLog 和 Agent trace。
3. 说“以后请用中文并且回答简洁”，随后问另一问题，展示 Profile 记忆创建和个性化。
4. 再说“改成详细解释”，展示旧偏好 superseded、新偏好 active。
5. 问“你记得我什么”，展示 memory intent 不走知识库 citation。
6. 开启“本轮不使用记忆”，展示 Message flag 和该轮不进入记忆/摘要。
7. 在 Memories 页面展示 pending 审批、revision 编辑、soft delete、restore、purge 与 update job。

### 9.2 演示前必须做

- 使用专门 demo 账号和无敏感数据的模型密钥，不投屏 `.env`。
- 提前确认 Document 为 `indexed`，Qdrant collection 维度与 embedding 模型一致。
- 跑端到端 smoke 和固定评估集，保存一份结果截图或 JSON。
- 修复测试环境隔离后在干净环境重跑 284 tests。
- 准备 Redis/Qdrant/Broker 故障时的口头降级说明，不建议现场故意破坏环境。
- 准备一张 Agent 图和一张 memory write flow，比直接翻代码更易讲清。

### 9.3 适合写进 README 或作品集的数字

只能写可重复测量的数字，例如：

- 后端测试数量与通过状态；
- Alembic migration 数量；
- 固定评估集的 Recall@K、MRR、citation hit rate；
- 在指定硬件、并发和数据集下的 P50/P95 首 token/总时延；
- 文档数、chunk 数、索引耗时和任务恢复时间。

不要编造“准确率提升 30%”“支持百万文档”“高并发”等没有基线、数据集和压测报告的数字。

---

## 10. 当前仓库验证快照

本次整理时实际执行结果：

| 检查 | 结果 |
|---|---|
| Python compileall | 通过 |
| Alembic 迁移链 | 20 个 migration 顺序升级通过 |
| 前端 TypeScript + Vite build | 通过；主 JS 583.72 kB，存在 >500 kB warning |
| 开发 Compose config | 通过 |
| 生产 Compose config | 通过 |
| 后端 unittest | 共 284；当前本机 `.env` 下 281 通过、3 失败 |
| 三个失败的原因复验 | 临时恢复 `MEMORY_PROFILE_LIMIT=20`、`MEMORY_SEMANTIC_LIMIT=5`、`QUERY_REWRITE_MAX_SUBQUERIES=3` 后，3 个用例全部通过 |

结论：当前失败表现为测试环境被本地配置污染，而不是这三个功能的实现回归；但在修复隔离并全量复跑前，不应在简历中写“284/284 全部通过”。

---

## 11. 最后给自己的答题原则

1. 先说业务不变量，再说框架名。
2. 先说 PostgreSQL 是真相，再解释 Redis/Qdrant 如何降级。
3. 说 Agent 时强调“受控”，不要冒充自治规划。
4. 说记忆时区分 history、summary、long-term memory 和 enterprise evidence。
5. 说可靠性时讲具体故障窗口：何时 commit、谁重试、怎样防旧 Worker 回写。
6. 说安全时不要只说 Prompt，要说后端校验、数据库约束和历史撤权。
7. 遇到未实现能力，直接说明当前边界和验证后的演进方案。

真正能让这个项目在面试中加分的，不是背出所有类名，而是能清楚回答：**为什么这样分层、失败时系统会留下什么、哪个存储是真相、模型能决定什么、模型绝对不能决定什么。**
