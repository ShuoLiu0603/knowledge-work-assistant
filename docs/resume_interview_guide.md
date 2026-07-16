# Agentic RAG 企业知识工作助手：简历与面试指南

> 本文只描述当前代码，不保留旧版 Supervisor、多 Agent、Query Rewrite、子问题拆分或加权 RRF 说法。
>
> 量化结果、判分口径和复现命令以 [项目量化结果](resume_metrics.md) 为准；本文只提供适合简历和面试的准确表达。

## 1. 项目定位

Knowledge Work Assistant 是一个面向企业知识场景的 Agentic RAG 工程参考实现。它把以下能力放进同一套可运行系统：

- 身份、部门、知识库角色与 L1-L5 文档密级；
- MinIO 原文存储、Celery 异步解析、PostgreSQL chunk 真相与 Qdrant 向量索引；
- Dense + BM25 + 无权重 RRF 混合检索；
- 单个 LangChain `create_agent` 工具循环；
- 固定核心画像、增量会话摘要与按需长期记忆；
- SSE、引用、Agent trace、检索日志、LLM 日志和审计；
- durable job、租约 fencing、指数退避与 Beat 恢复。

准确定位是“工程化参考实现”，不是已经经过真实生产流量和多区域高可用验证的成品。

## 2. 可用于简历的表述

### 推荐版

**Knowledge Work Assistant｜Agentic RAG 企业知识工作助手**  
Python / FastAPI / LangChain / PostgreSQL / Redis / Qdrant / Celery / MinIO / React / TypeScript

- 设计受控 Agent 工具循环，向单个 LangChain Agent 提供 `memory(query)` 与 `rag(query)` 两个工具，支持直接回答、按需检索、不同 query 的多次检索、累计证据和最终无工具收口；25 个 golden cases 连续运行 3 轮，在 75 条轨迹上取得 **98.67% 严格轨迹准确率、100% 工具类型准确率**。
- 构建权限感知的 Dense + BM25 + 无权重 RRF 混合检索链路，PostgreSQL 保存文档、chunk 与权限真相，Qdrant 保存可重建向量；在 BEIR/SciFact 完整 test 上取得 **nDCG@10 68.47%、Recall@10 83.22%、MRR@10 64.62%**。
- 实现分层记忆：核心 Profile 固定注入，普通长期记忆仅在 Agent 调用 Memory 时按需召回；回答后通过 Candidate Extractor、相关旧记忆检索、Memory Judge 抽象关系裁决与确定性校验完成最终写入。LongMemEval-S 子集取得 **Turn Recall Any@5 95.83%、Recall All@5 83.33%、Top-5 Reader QA 83.33%**。
- 使用 Celery durable job、幂等键、租约 fencing、指数退避和 Beat 扫描恢复文档、记忆、摘要与外部清理任务；建立 **310 项后端回归、24 个 Alembic revision、前端生产构建与端到端 smoke** 的统一质量门禁。

### 一页简历压缩版

- 基于 FastAPI、LangChain、PostgreSQL、Qdrant 与 Celery 构建权限感知的 Agentic RAG 系统，支持单 Agent 自主选择直接回答、Memory 与 RAG，并在预算内多次更换 query 检索。
- 实现 Dense + BM25 + 无权重 RRF、上下文预算和可追溯引用；SciFact 完整 test 达到 nDCG@10 68.47%、Recall@10 83.22%、MRR@10 64.62%。
- 设计固定画像 + 按需长期记忆及两阶段写入治理；LongMemEval-S Turn Recall Any@5 95.83%，项目 golden trajectory 严格准确率 98.67%。

### 不应写的表述

| 不准确表述 | 当前事实 |
|---|---|
| 多 Agent 协作、Supervisor 调度 | 当前是一个 LangChain `create_agent` 循环 |
| 独立 RAG/Memory/Summary/Writing Agent | 当前只有 `memory(query)` 与 `rag(query)` 两个工具 |
| Query Rewrite、子问题拆分 | 检索层一次只处理 Agent 给出的一条 query |
| 加权 RRF | 当前 Dense/BM25 使用无权重 RRF，`RRF_K=60` |
| 已实现 cross-encoder reranker | 当前 `reranker_enabled=false` |
| 测试覆盖率 100% | 当前只有 310/310 回归通过，没有语句/分支覆盖率报告 |
| 生产级高可用 | 当前生产 Compose 是单机部署参考 |
| 引用保证答案事实正确 | 引用只证明证据被提供给模型，不是逐句事实核验 |

## 3. 面试开场

### 30 秒版本

> 我做了一个企业知识场景的 Agentic RAG 助手。系统不是固定“每次都检索”，而是让一个受控 Agent 在直接回答、用户长期记忆和企业知识库之间选择；如果证据还不够，可以在预算内换 query 再查。后端始终控制知识库权限、文档密级、引用来源、模型调用上限和最终收口。检索采用 Dense + BM25 + RRF，记忆采用固定核心画像、按需召回和两阶段写入治理，并用 Celery durable job 与 Beat 恢复异步任务。

### 两分钟版本

> 文档上传后，原文进入 MinIO，PostgreSQL 创建文档记录，Celery 异步解析、切分和生成 Embedding。chunk 正文、状态、密级和业务关系保存在 PostgreSQL，向量写入 Qdrant。
>
> 对话时，服务层先计算用户可访问的知识库范围。Agent 是单个 LangChain `create_agent` 循环，模型可以直接回答，也可以调用 `memory(query)` 或 `rag(query)`。每次 RAG 调用只处理一条模型生成 query，内部并行走 Dense 和 BM25，再用无权重 RRF 融合。多次检索的证据按 chunk ID 去重并保留稳定引用编号。
>
> 记忆读取和写入是解耦的。回答前固定注入姓名、称呼、角色、语言和稳定响应偏好；项目、决策、事件等普通记忆只有调用 Memory 时才查询。回答提交后，第一模型只提取候选，系统检索相关旧记忆，第二模型裁决操作，最后还要经过 evidence、敏感信息、目标归属、revision、唯一约束和事务校验。
>
> 工程上记录 AgentRun、RetrievalLog、LlmCallLog、引用和审计，并用 durable job、租约 fencing、指数退避和 Beat 扫描处理进程退出、重复投递和 broker 短暂故障。

## 4. 当前架构与主链路

### 4.1 文档入库

```text
upload
→ permission and file checks
→ MinIO original file
→ PostgreSQL document(status=uploaded)
→ Celery process_document
→ parse / clean / chunk
→ PostgreSQL document_chunks
→ Embedding
→ Qdrant upsert
→ document(status=indexed)
```

### 4.2 Agent 对话

```text
authorize conversation and knowledge-base scope
→ persist user Message
→ load core profile + summary + recent messages
→ model decides
   ├─ answer directly
   ├─ memory(query) → merge recalled memories → decide again
   └─ rag(query) → merge evidence/citations → decide again
→ final tool-free response when required
→ persist AgentRun and assistant Message
→ run or enqueue two-stage memory update
→ dispatch conversation-summary update
```

### 4.3 RAG

```text
Agent query
→ authorized scope
→ Dense(Qdrant) + BM25(PostgreSQL)
→ PostgreSQL hydration and permission recheck
→ unweighted RRF
→ Top-K and optional verified extractive compression
→ RetrievalLog + accumulated evidence
```

检索层不再调用 LLM 做改写或拆分。多次检索能力来自外层 Agent：模型看到上一批结果后，可以针对尚未解决的信息缺口生成新的 query。

### 4.4 Memory

读取：

```text
always: core profile + conversation summary + recent messages
on demand: memory(query)
→ Qdrant semantic recall
→ bounded PostgreSQL fallback
→ dedupe by memory id
→ memory-context budget
```

写入：

```text
current user/assistant turn
→ Candidate Extractor
→ exact/canonical/category + Qdrant + PostgreSQL related-memory retrieval
→ Memory Judge: independent/equivalent/refinement/replacement/uncertain/discard
→ relation-to-action mapping + evidence / sensitivity / target / revision / uniqueness checks
→ one database transaction
→ best-effort Qdrant sync and later reconcile
```

## 5. 关键代码地图

| 能力 | 主要位置 |
|---|---|
| Agent 状态与 trace | `apps/backend/app/agents/state.py` |
| Agent 循环、工具、动态 Prompt | `apps/backend/app/agents/runtime.py` |
| 核心记忆加载与回答后更新 | `apps/backend/app/agents/memory_agent.py` |
| RAG 工具服务入口 | `apps/backend/app/services/qa_service.py` |
| Dense/BM25/RRF | `apps/backend/app/rag/advanced_retrieval.py` |
| 上下文压缩 | `apps/backend/app/llm/context_compression.py` |
| Memory 召回与两阶段写入 | `apps/backend/app/services/memory_service.py`、`apps/backend/app/memory/` |
| 会话、SSE 与并发控制 | `apps/backend/app/services/conversation_service.py` |
| 异步任务与恢复 | `apps/backend/app/workers/` |
| 权限与知识库范围 | `apps/backend/app/services/knowledge_base_service.py` |
| 配置与生产校验 | `apps/backend/app/core/config.py` |

## 6. 高频追问

### Q1：为什么使用 Agent，而不是固定一次 RAG？

固定 RAG 对寒暄、文本改写和已被上下文回答的问题也会产生额外成本；复杂问题又可能一次检索不够。当前 Agent 可以选择不检索，也可以根据结果发现新的信息缺口并换 query。自由度只放在“何时调用两个工具、query 写什么”，权限范围和数据源边界仍由后端决定。

### Q2：为什么没有做多个子 Agent？

当前任务只有两个清晰的外部信息源，拆成 Supervisor、RAG Agent、Memory Agent 和 Reviewer 会增加模型调用、状态同步和调试成本，却没有证明能提升效果。单 Agent + 两个窄工具更容易观察和评估。若以后出现代码执行、审批、外部搜索等相互独立且有不同权限的能力，再考虑子 Agent。

### Q3：最终如何强制回答？

中间件在最后一次模型调用移除全部工具；总工具预算被检测为耗尽后，下一次模型调用也不再获得工具。Prompt 要求基于已有上下文回答或明确说明证据不足。模型调用次数有硬上限。

需要准确说明当前缺口：总工具和分工具预算是在模型调用前通过解绑工具实施，工具执行入口尚无第二道硬校验，兼容模型若重复输出历史 tool call，理论上可能越过声明的分预算。

### Q4：为什么 Memory 后还可能调用 RAG？

Memory 只提供用户个人偏好、项目和历史决策，RAG 才能提供企业制度和文档事实。一个问题同时包含“按我的偏好”和“根据公司制度”时，先查 Memory 再查 RAG 是合理轨迹。Memory miss 不能成为去 RAG 搜索个人信息的理由。

### Q5：为什么移除 Query Rewrite 和子问题拆分？

外层 Agent 已经可以多次生成不同 query。若检索层内部再做改写与拆分，会造成 query 数量乘法增长，并让日志难以回答“哪条 query 找回了什么”。当前设计把一次 `rag(query)` 保持为确定的两路召回和一次融合，把多步规划留给 Agent。

### Q6：为什么 Dense + BM25？

Dense 擅长同义表达和语义相关性，BM25 擅长编号、术语、文件名和专有名词。两路分数尺度不同，因此使用只依赖名次的 RRF 融合。当前是无权重 RRF，`RRF_K=60`，每路模板候选深度为 15。

### Q7：为什么 PostgreSQL 和 Qdrant 都要保留？

PostgreSQL 保存文档状态、权限、chunk 正文和长期记忆真相；Qdrant 只保存可重建向量。Dense 命中后必须回 PostgreSQL hydration 并重新校验。这样 Qdrant 漂移或陈旧 point 不会成为最终授权依据。

### Q8：多次 Memory 为什么不会只保留最后一批？

每次召回结果合并到当前 `AgentRunState.long_term_memories`，按 Memory ID 去重，并记录批次。后续调用重新构造 Memory context。单次召回和最终进入上下文的数量不同：模板中单次普通召回上限为 6，一轮累计格式化上限为 10，最终还受 1600 token 与 6000 字符保险线约束。

### Q9：记忆冲突如何处理？

第一模型不能直接指定旧记忆或决定 update/supersede。系统先检索相关旧记忆，第二模型只能判断抽象关系，并在需要时从给定候选集合内选择目标。后端再校验 evidence 必须来自当前 user Message、目标属于当前用户、revision 未过期、敏感信息规则和数据库唯一约束。独立事实 create，同一事实补充 update，明确替代 supersede，不确定时 pending，非法或失败时 fail-closed；向量分数不触发自动 merge。

### Q10：会话摘要如何避免把助手建议当成用户决定？

摘要 Prompt 明确区分用户请求、接受、拒绝、纠正和禁止，与助手提议、工具已完成事实。它输出结构化工作状态，不是逐轮复述，也不是用户画像。模型不被要求估算 token；程序测量超限后用专用压紧 Prompt 重试，最后按约束、目标、阻塞/下一步、已确认事实、重要产物的优先级保留完整信息单元。

### Q11：引用能保证什么？

引用能保证最终返回的 citation 指向本轮实际提供给模型的授权 chunk，并能追溯 RetrievalLog。它不能证明模型的每一句话都被对应 chunk 严格蕴含，因此仍需要引用一致性评估和高风险场景人工复核。

### Q12：异步任务为什么需要 durable job 和 lease fencing？

直接调用 Celery 后再忘记任务会在 broker 故障时丢工作；只有幂等键又不能阻止旧 worker 在 lease 过期后覆盖新 worker。系统先在 PostgreSQL 写 job，再 dispatch；worker claim lease 并携带 token，提交前验证 token。Beat 扫描 queued、failed 或 lease 过期任务恢复执行。

## 7. 指标如何解释

| 指标 | 当前结果 | 只能说明什么 |
|---|---:|---|
| SciFact nDCG@10 | 68.47% | 检索排序质量 |
| SciFact Recall@10 | 83.22% | 相关文档进入前 10 的比例 |
| Agent trajectory | 98.67%（74/75） | 受控 observation 下工具轨迹是否符合 golden |
| LongMemEval Turn Recall Any@5 | 95.83% | 至少一个相关历史 turn 是否进入 Top-5 |
| LongMemEval Top-5 Reader QA | 83.33% | 给定检索结果后的读取与回答 |
| 后端回归 | 310/310 | 当前自动化用例全部通过，不等于覆盖率 100% |

公开数据集子集、Reader-only 和 LLM-as-Judge 结果不能写成官方榜单成绩。简历优先使用数据范围、指标名称、结果和评估边界四要素完整的表述。

## 8. 当前边界与下一步

- 工具执行入口需要增加与声明预算一致的第二道硬校验。
- 当前没有 cross-encoder reranker；是否引入应由公开基准和真实业务集的增益/延迟决定。
- BM25 由项目代码实现，适合当前规模和可解释性；大规模部署可评估 PostgreSQL FTS、OpenSearch 或 Elasticsearch。
- Qdrant Memory index 故障时只有有界 PostgreSQL 回退，极旧且不在候选窗口的普通记忆可能漏召回。
- 前端令牌仍保存在 `localStorage`；公网部署应迁移 refresh token，并补齐 OIDC/MFA、边缘限流与浏览器安全策略。
- 生产 Compose 是单机参考，不包含多区域高可用、自动扩缩容和灾难恢复编排。

面试时把这些边界说清楚，比把项目包装成“完美生产系统”更可信。
