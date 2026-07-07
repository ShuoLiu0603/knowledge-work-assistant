# 面试备战指南

这份文档的目标不是再写一份 README，而是帮助你在面试里把这个项目讲成一个完整、可信、能经得起追问的工程项目。

你要做到三件事：

1. 能用 1 到 3 分钟讲清楚项目价值和主链路。
2. 能在追问时落到具体模块、函数、表结构和技术取舍。
3. 能诚实说明当前边界，以及如果继续迭代会怎么做。

## 1. 你应该掌握什么

面试官不会只问“你用了什么技术栈”。他们更关心你是否真的理解这些技术为什么放在这里，以及出了问题你怎么定位。

### 1.1 必须掌握的主线

你至少要能不看代码讲清楚这五条链路：

| 链路 | 你要讲清楚什么 | 核心代码 |
|---|---|---|
| 文档入库 | 上传、去重、MinIO、Celery、解析、切分、Embedding、Qdrant | `document_service.py`、`document_tasks.py`、`loaders.py`、`splitters.py`、`vector_store.py` |
| RAG 问答 | 组织/密级权限校验、检索范围解析、Query Planning、Dense + BM25、RRF、上下文压缩、LLM 回答、引用 | `knowledge_base_service.py`、`qa_service.py`、`advanced_retrieval.py`、`answering.py` |
| Agent 编排 | Memory、Supervisor、RAG/Memory/Chat/Summary/Writing、trace | `agents/graph.py`、`supervisor.py`、`rag_agent.py` |
| 记忆系统 | 短期记忆、长期记忆、active/pending/superseded/ignored、去重、冲突覆盖 | `memory_service.py` |
| 可观测性 | retrieval logs、LLM logs、agent runs、audit logs、feedback、admin metrics | `retrieval_log_service.py`、`llm_log_service.py`、`agent_service.py`、`audit_service.py` |

### 1.2 必须掌握的技术点

你需要能解释这些概念，而不是只说“我用了”：

| 技术点 | 面试里应该怎么理解 |
|---|---|
| FastAPI | API 层只做路由、依赖注入和请求响应，复杂业务放 Service |
| SQLAlchemy | 保存用户、知识库、文档、chunk、会话、日志、记忆等结构化数据 |
| Qdrant | 存 chunk 向量，用 payload filter 做知识库和密级过滤 |
| MinIO | 保存原始上传文件，数据库只保存 object key |
| Celery + Redis | 文档解析和向量化是耗时任务，异步处理避免上传接口阻塞 |
| Dense Retrieval | 用 embedding 找语义相似 chunk |
| BM25 Retrieval | 用词项匹配找配置项、制度名、函数名、数字等精确内容 |
| RRF | 不直接混合 Dense 分数和 BM25 分数，而是按排名融合 |
| SSE | 后端逐 token 推送，前端实时渲染 |
| LangGraph | 可选编排后端，Agent 节点仍复用已有 RAG service |
| Prompt 约束 | 事实问题只能基于知识库上下文，记忆不能当事实依据 |
| RAG 评估 | Recall@K、MRR、citation_hit_rate 用来衡量检索和引用质量 |

## 2. 一句话介绍

如果面试官说“简单介绍一下你的项目”，可以这样说：

> 这是一个面向企业制度文档的 Agentic RAG 知识库助手。它支持用户鉴权、部门/公开/私有知识库、L1-L5 文档清级、文档异步入库、基于 Dense + BM25 + RRF 的可解释检索、带引用回答、SSE 流式对话、Agent trace、短期/长期记忆，以及检索日志和评估脚本。我的重点不是做一个聊天 demo，而是把文档上传到可检索、可引用、可审计、可评估的工程闭环跑通。

这段话里有几个关键词：

- 企业制度文档：明确业务场景。
- 工程闭环：区别于简单 demo。
- Dense + BM25 + RRF：体现 RAG 检索设计。
- 权限、引用、日志、评估：体现工程化和可追溯。
- Agent 和记忆：体现扩展能力，但不喧宾夺主。

## 3. 1 分钟讲法

> 项目解决的是企业内部制度、流程、报销规则等文档难查的问题。用户可以创建知识库并上传 PDF、DOCX、Markdown、CSV 等文件，后端把原文件存到 MinIO，Celery worker 异步解析、切分、生成 embedding，并把向量写入 Qdrant，同时把 chunk 正文和元数据写入 PostgreSQL。  
>
> 用户提问时，系统先解析检索范围：当前库、本部门或全部可访问库，再叠加组织边界和 L1-L5 清级过滤，然后构造记忆上下文进入 RAG 检索。检索层保留原始问题，并在复杂问题下拆分最多 3 个 sub-queries。每个 query 同时走 Dense 向量召回和 BM25 正文召回，候选按 chunk id 去重后用加权 RRF 融合，最后选 top K 做上下文压缩，交给 LLM 生成带引用的回答。  
>
> 对话层支持 SSE 流式输出，Agent 层用 Memory、Supervisor、RAG、Summary、Writing 等节点编排，但所有节点都复用同一套 RAG service。系统还记录 retrieval logs、LLM logs、agent trace、audit logs，并提供 Recall@K、MRR、citation_hit_rate 的轻量评估脚本。

## 4. 3 分钟讲法

如果面试官让你详细讲项目，可以按这个顺序：

### 4.1 先讲业务问题

企业知识库问答的核心不是“能聊天”，而是：

- 文档多，用户不想全文搜索。
- 回答必须有依据，不能凭通用知识编造。
- 不同用户可访问的数据范围不同。
- 文档上传、解析、向量化需要异步处理。
- 回答质量需要可解释、可追踪、可评估。

### 4.2 再讲整体架构

```text
React 前端
  -> FastAPI API
  -> Service 层
  -> PostgreSQL / Redis / Qdrant / MinIO
  -> Celery Worker
  -> OpenAI-compatible LLM / Embedding provider
```

各组件职责：

| 组件 | 职责 |
|---|---|
| React + Vite | 登录、知识库、文档、聊天、引用、检索解释、管理端 |
| FastAPI | API 路由、鉴权依赖、请求响应 |
| Service 层 | 业务流程编排和权限边界 |
| PostgreSQL | 用户、知识库、文档、chunk、会话、日志、记忆 |
| Redis | Celery broker 和短期记忆 |
| Qdrant | chunk 向量和 payload filter |
| MinIO | 原始上传文件 |
| Celery Worker | 文档解析、切分、embedding、向量入库 |
| LLM Provider | Agent 路由、答案生成、摘要、记忆抽取 |

### 4.3 再讲文档入库

```text
用户上传文件
-> FastAPI 接收 multipart
-> 校验权限、扩展名、大小、content_hash 去重
-> 原文件上传 MinIO
-> PostgreSQL 创建 documents 记录
-> Celery 投递 process_document
-> Worker 下载原文件
-> parse_document 解析
-> split_blocks 切分
-> 写 document_chunks
-> embedding provider 批量生成向量
-> upsert 到 Qdrant
-> documents.status = indexed
```

对应代码：

- 上传入口：`apps/backend/app/services/document_service.py`
- 异步任务：`apps/backend/app/workers/document_tasks.py`
- 文档解析：`apps/backend/app/rag/loaders.py`
- 文本切分：`apps/backend/app/rag/splitters.py`
- 向量入库：`apps/backend/app/rag/vector_store.py`

可以强调的工程点：

- 上传接口不做耗时解析，避免 HTTP 请求阻塞。
- 原文件不直接进数据库，而是进入 MinIO。
- `content_hash` 防止同一知识库重复上传相同文件。
- 文档状态有 `uploaded -> parsing -> chunking -> embedding -> indexed`，失败则 `failed` 并保存错误信息。
- 重新处理文档时会删除旧 chunk 和旧向量，避免重复召回。

### 4.4 再讲 RAG 检索

当前 RAG 链路：

```text
question
-> plan_retrieval_queries
   -> original query
   -> normalized query
   -> sub_queries, max 3
-> Dense retrieval
-> BM25 retrieval
-> dedupe by chunk_id
-> weighted RRF
-> top K
-> context compression
-> grounded answer
```

对应代码：

- RAG service：`apps/backend/app/services/qa_service.py`
- 检索主逻辑：`apps/backend/app/rag/advanced_retrieval.py`
- Dense 检索：`apps/backend/app/rag/retrieval.py`、`vector_store.py`
- 回答生成：`apps/backend/app/rag/answering.py`

核心设计点：

1. 原始问题必须保留  
   LLM 或规则拆分可能丢信息，所以第一条检索 query 永远是用户原问题。

2. sub-query 数量受控  
   最多 3 个，避免召回池过大导致噪声上升。

3. Dense 和 BM25 分工明确  
   Dense 解决语义相似，BM25 解决精确词项。

4. 不直接相加分数  
   Dense score 和 BM25 score 不在同一分数空间，所以用 RRF 按排名融合。

5. 原始问题权重更高  
   `dense_original` 和 `bm25_original` 权重是 `1.2`，sub-query route 权重是 `1.0`，防止子问题反客为主。

### 4.5 再讲回答生成

回答不是直接把检索结果塞给模型，而是：

```text
selected chunks
-> 控制总上下文长度
-> 每个 chunk 压缩到合适长度
-> 格式化为 [1]、[2]、[3] 来源上下文
-> LLM 只基于 Knowledge context 回答
-> 返回 answer + citations
```

Prompt 约束：

- 只基于知识库上下文回答事实问题。
- 上下文不足时明确说明依据不足。
- 使用 `[1]`、`[2]` 这种引用标记。
- Memory 只用于偏好和对话状态，不能作为事实依据。

### 4.6 最后讲 Agent 和记忆

Agent 不是重新写一套 RAG，而是编排已有能力：

```text
load_memory
-> supervisor
-> rag_agent / memory answer / chat / summary / writing
-> update_memory
```

Supervisor 使用 LLM 提出 intent，但服务层会保守归一化：

- 企业事实、制度、流程、报销、合同等默认走 RAG。
- 明确问“你记得我什么”才走 memory answer。
- 纯寒暄才走 chat。
- summary 和 writing 仍然复用 RAG service 获取依据。

记忆系统分三层：

- Redis 短期记忆：最近几轮对话。
- `conversations.summary`：会话摘要。
- `user_memories`：长期稳定偏好。

长期记忆状态：

| 状态 | 含义 |
|---|---|
| `active` | 可以进入回答上下文 |
| `pending` | 需要用户审核，不进入回答上下文 |
| `superseded` | 被新记忆覆盖 |
| `ignored` | 忽略 |

## 5. 模块地图

面试官如果让你现场说“某个能力在哪实现”，你可以这样定位：

| 能力 | 文件 |
|---|---|
| 应用启动 | `apps/backend/app/main.py` |
| 配置读取 | `apps/backend/app/core/config.py` |
| JWT、密码哈希 | `apps/backend/app/core/security.py` |
| 用户注册登录 | `apps/backend/app/services/auth_service.py` |
| 知识库权限 | `apps/backend/app/services/knowledge_base_service.py` |
| 文档上传 | `apps/backend/app/services/document_service.py` |
| Worker 入库 | `apps/backend/app/workers/document_tasks.py` |
| 文档解析 | `apps/backend/app/rag/loaders.py` |
| 文本切分 | `apps/backend/app/rag/splitters.py` |
| Embedding | `apps/backend/app/rag/embeddings.py` |
| Qdrant | `apps/backend/app/rag/vector_store.py` |
| RAG 检索 | `apps/backend/app/rag/advanced_retrieval.py` |
| RAG 回答 | `apps/backend/app/rag/answering.py` |
| QA service | `apps/backend/app/services/qa_service.py` |
| Agent 图 | `apps/backend/app/agents/graph.py` |
| Supervisor | `apps/backend/app/agents/supervisor.py` |
| Agent RAG/chat/memory answer | `apps/backend/app/agents/rag_agent.py` |
| 长期记忆 | `apps/backend/app/services/memory_service.py` |
| 流式对话 | `apps/backend/app/services/conversation_service.py` |
| 检索日志 | `apps/backend/app/services/retrieval_log_service.py` |
| LLM 日志 | `apps/backend/app/services/llm_log_service.py` |
| RAG 评估 | `apps/backend/app/evaluation/metrics.py`、`scripts/run_eval.py` |

## 6. RAG 追问准备

### 6.1 为什么要 Dense + BM25

标准回答：

> Dense retrieval 适合语义相似，比如“住宿标准”能找到“酒店报销上限”。但它对精确词、数字、变量名、配置项、文件名有时不稳定。BM25 适合精确词项匹配，比如 `JWT_SECRET_KEY`、`RETRIEVAL_ROUTE_LIMIT`、发票、报销、600 元这种信息。两者互补，所以我让原始问题和 sub-queries 同时走 Dense 和 BM25，再用 RRF 融合。

### 6.2 为什么不用直接加权相加

标准回答：

> Dense score 是向量相似度，BM25 score 是词项相关性，它们不是同一个分数空间。直接相加会让某一路的分数尺度主导排序。RRF 只依赖每条 route 内部的排名，不依赖原始分数，所以更适合融合异构检索器。

### 6.3 RRF 公式怎么讲

项目里核心公式：

```text
rrf_score = sum(route_weight / (rrf_k + rank))
```

解释：

- `rank` 是候选在某条 route 里的排名。
- `rrf_k` 默认 60，避免头部排名差距过大。
- 一个 chunk 如果被多条 route 同时召回，分数会累加。
- 原始问题 route 权重是 1.2，sub-query route 权重是 1.0。

示例：

```text
dense_original: A, B, C
bm25_original:  B, D, A
dense_subquery_1: E, A
```

最终 A 和 B 因为多次出现，会比单一路线命中的候选更稳。

### 6.4 Query Planning 做了什么

当前实现是保守规则规划，不是让 LLM 随意改写：

- 保留原始问题。
- 归一化问题用于日志和拆分。
- 根据连接词和标点拆分最多 3 个 sub-queries。
- 不做关键词扩展。
- 不做 metadata route。
- 不做 reranker。

为什么这样做：

> 当前阶段更重视链路可解释和可测试。原始问题加受控 sub-query 已经能覆盖复杂问题，避免 LLM 改写带来不可控噪声。

### 6.5 为什么现在不用 reranker

标准回答：

> reranker 很有价值，但它会引入额外模型成本、延迟和调试复杂度。当前项目重点是把基础 RAG 链路做清楚：Dense + BM25 多路召回、RRF 融合、引用对齐、日志可解释、评估可重复。没有 reranker 的情况下，检索链路更容易分析。如果后续要提升最终排序质量，可以在 RRF 后接 cross-encoder reranker，但需要配套评估对比。

### 6.6 为什么现在不用 metadata retrieval

标准回答：

> metadata retrieval 对文件名、标题路径很有帮助，但它也容易让“文件名擦边命中”压过真正相关正文。当前版本先把检索内容限定为原始问题和 sub-queries，只在 chunk 正文上做 Dense 和 BM25，链路更简洁。未来如果用户经常按文件名、章节名查询，可以再把 metadata route 作为单独增强项接入 RRF。

### 6.7 BM25 是怎么实现的

当前实现位置：`advanced_retrieval.py`

流程：

```text
query -> text_terms
-> 数据库正文 ilike 预过滤候选
-> 统计 total_chunks、avgdl、document_frequency
-> 按 BM25 公式计算每个候选分数
-> route 内排序
```

可以诚实说明：

> 当前 BM25 是轻量工程实现，适合项目演示和中小规模知识库。大规模生产环境可以换成 PostgreSQL FTS、Elasticsearch/OpenSearch 或专门的 sparse index，但接口层可以保持为 BM25 route，不影响 RRF 融合框架。

### 6.8 中文怎么处理

当前 `text_terms()` 对中文会生成 bigram，比如“住宿报销”会产生相邻二字词。这样比单字匹配更稳，也不依赖额外中文分词服务。

诚实边界：

> 这不是最强中文检索方案。生产环境可以接 jieba、HanLP、ES 中文 analyzer，或者换成更专业的 sparse retrieval。但当前版本优先保持依赖简单和链路可解释。

## 7. 权限和安全追问准备

### 7.1 知识库权限怎么设计

知识库有两类：

| 类型 | 规则 |
|---|---|
| private | 普通用户可创建，默认 owner 可管理，成员按 owner/editor/viewer 控制 |
| public | 管理员创建和维护，所有登录用户可读，但按 security_level 过滤 |

角色：

- owner：拥有者。
- editor：可上传和管理私有/部门知识库文档。
- viewer：只读。

### 7.2 文档密级怎么控制

用户有 `security_level`，文档和 chunk 也有 `security_level`。

公开知识库：

```text
只返回 document/chunk.security_level <= user.security_level
```

私人知识库：

```text
只按成员权限隔离，不额外按文档密级过滤
```

Dense 检索在 Qdrant payload filter 里过滤：

```text
user_id = knowledge_base.owner_id
knowledge_base_id = 当前知识库
security_level <= max_security_level
```

BM25 检索在 PostgreSQL 查询里过滤：

```text
DocumentChunk.knowledge_base_id == kb_id
DocumentChunk.security_level <= max_security_level
```

### 7.3 为什么 Qdrant payload 里要存 owner_id

因为项目使用单一 Qdrant collection 存所有知识库的向量。payload 里保存：

- `user_id`
- `knowledge_base_id`
- `document_id`
- `chunk_id`
- `security_level`

检索时同时按 owner、knowledge_base、security_level 过滤，避免跨用户、跨知识库、跨密级泄漏。

## 8. 记忆系统追问准备

### 8.1 记忆为什么不能直接追加进 prompt

标准回答：

> 如果把所有历史对话都直接追加进 prompt，会造成上下文膨胀、隐私风险和记忆污染。我的设计是短期记忆保存最近几轮，长期记忆只保存稳定偏好，并且要经过 LLM 候选抽取和后端规则裁决。只有 active 记忆会进入回答上下文，pending 不会参与回答。

### 8.2 记忆写入流程

```text
用户输入
-> LLM 抽取 durable preference/profile/project/instruction 候选
-> 后端归一化
-> content_hash 精确去重
-> 置信度和敏感度判断
-> pending / ignore / active
-> 同类记忆查冲突
-> 语义相似则 merge
-> 冲突则 supersede
-> 新偏好则 create
```

### 8.3 pending 记忆有什么用

标准回答：

> pending 用来处理置信度不足或敏感度不低的记忆候选。它会落库，方便用户审核，但不会进入回答上下文。这样可以降低误记和敏感信息污染回答的风险。

### 8.4 记忆和知识库事实怎么隔离

标准回答：

> 记忆只用于理解用户偏好和会话状态，不能作为事实依据。比如用户偏好“回答简洁”可以影响输出风格，但用户曾经说“住宿标准是 500”不能当制度依据。制度事实必须来自知识库检索 chunk，并带 citation。

## 9. Agent 追问准备

### 9.1 Agent 到底做了什么

标准回答：

> Agent 不是自主乱调工具，而是一个受控编排层。它先加载记忆，再由 Supervisor 判断 intent，然后路由到 RAG、Memory answer、Chat、Summary 或 Writing。RAG、Summary、Writing 都复用同一个 RAG service，不绕过权限校验。每个节点都会写 trace，方便解释这次回答为什么这么走。

### 9.2 Supervisor 为什么还要后端归一化

标准回答：

> LLM 可以做语义判断，但不能完全信任。比如企业制度问题如果被误判成 chat，就会绕过检索。我的设计是 LLM 先提出 intent，后端用规则做保守裁决：企业事实默认回 RAG，明确问记忆才走 memory，纯寒暄才走 chat。

### 9.3 Summary/Writing 为什么也要 RAG

标准回答：

> 总结和写作如果不检索知识库，就容易变成通用大模型输出。项目里 Summary 和 Writing 都先调用 RAG service 获取依据，再基于 grounding 生成摘要或草稿，这样仍然可追溯。

## 10. 流式输出追问准备

### 10.1 SSE 怎么做的

代码位置：`conversation_service.py`

流程：

```text
保存 user message
-> 写入短期记忆
-> yield trace started
-> 后台线程运行 Agent
-> token callback 放入 Queue
-> 主线程从 Queue 读 token 并 yield SSE
-> Agent 完成后发送 agent_run、retrieval_log、citations
-> 保存 assistant message
-> 绑定 retrieval_log 和 agent_run 到 message
-> 写入短期记忆
-> 必要时更新会话 summary
```

### 10.2 为什么用线程 + Queue

标准回答：

> SSE 响应需要持续向前端输出事件，而 Agent/LLM 调用是阻塞流程。用后台线程跑 Agent，通过 Queue 把 token 传给主生成器，可以让主线程持续 yield SSE event。这样不需要引入更复杂的异步任务系统，也能实现流式体验。

## 11. 日志和评估追问准备

### 11.1 retrieval log 记录什么

`retrieval_logs` 记录：

- 原始问题。
- normalized query。
- sub_queries。
- 实际检索 query 集合。
- retrieval_routes。
- candidates。
- selected_chunks。
- rrf_k。
- compression_chars_saved。

用途：

- 看问题有没有被拆分。
- 看 Dense/BM25 哪条 route 命中。
- 看候选排序和最终选中的 chunk。
- 定位“没搜到”还是“搜到了但回答没用好”。

### 11.2 LLM log 记录什么

`llm_call_logs` 记录：

- provider。
- model_name。
- prompt/completion/total tokens。
- latency。
- status。
- error_message。
- agent_name。

用途：

- 成本估算。
- 延迟排查。
- 失败追踪。
- 区分 supervisor、rag_agent、memory_extractor 等调用来源。

### 11.3 评估指标怎么解释

| 指标 | 解释 |
|---|---|
| Recall@K | top K 引用中是否包含预期来源，衡量有没有找回来 |
| MRR | 第一个正确引用排得越靠前越好，衡量排序质量 |
| citation_hit_rate | 是否至少给出一个正确来源引用 |
| answer_keyword_hit_rate | 答案是否包含预期关键词，只是弱信号 |

答题重点：

> RAG 不能只看答案像不像，还要看引用是否命中。因为答案可能被 LLM 写得很自然，但依据错了。项目里用 citation 指标约束 grounded answer 的可信度。

## 12. 典型面试问答

### Q1：你这个项目和普通 ChatGPT 套壳有什么区别

答：

> 普通套壳通常只是把用户问题发给模型。这个项目做的是企业知识库闭环：文档上传、异步解析、切分、embedding、向量入库、权限过滤、多路检索、引用回答、Agent trace、长期记忆、日志和评估。模型只是最后生成回答的一环，核心价值在数据入库、可控检索、权限安全和可追溯。

### Q2：为什么文档处理要异步

答：

> 解析 PDF/DOCX、切分文本、调用 embedding、写 Qdrant 都可能很慢。如果放在上传接口同步做，用户请求会超时，也不利于失败重试。现在上传接口只保存文件、建记录、投递 Celery 任务，Worker 后台处理并更新状态，前端轮询状态。

### Q3：chunk size 和 overlap 为什么这么设计

答：

> chunk 太大，embedding 表达会混杂，LLM 上下文也浪费；chunk 太小，语义不完整，容易丢上下文。overlap 是为了避免重要信息刚好落在边界被切断。当前默认 `DEFAULT_CHUNK_SIZE=800`、`DEFAULT_CHUNK_OVERLAP=120`，是演示和制度文档场景下的折中，后续可以通过 Recall@K 和人工评估调参。

### Q4：如果检索不到怎么办

答：

> 系统仍会调用 LLM，但 prompt 要求它说明知识库依据不足，不允许用通用知识编造。检索日志会记录 candidates 和 selected_chunks，方便判断是文档未入库、权限过滤、query 拆分、Dense/BM25 召回还是上下文压缩的问题。

### Q5：怎么防止越权检索

答：

> API 层和 service 层先用 `ensure_kb_access` 与 `resolve_search_scope` 校验知识库权限和检索范围。Dense 检索在 Qdrant payload filter 中限制 owner、knowledge_base_id 和 security_level。BM25 检索在 PostgreSQL 查询中限制 knowledge_base_id 和 security_level。公开/部门知识库按用户清级过滤，私有知识库按成员权限隔离。

### Q6：为什么 Agent 不直接决定是否检索

答：

> Supervisor 可以建议 intent，但后端要保守归一化。企业事实问题如果误判成 chat 就会绕过知识库，风险很高。所以模型只提出建议，服务层做最终裁决。这个设计比完全相信 LLM 更稳。

### Q7：长期记忆会不会污染回答

答：

> 会，所以我做了隔离。记忆上下文只用于偏好和会话状态，不作为知识事实。长期记忆写入要经过 LLM 候选抽取和后端规则判断，低置信度或敏感内容进入 pending，pending 不会参与回答。Prompt 也明确要求 memory 不能当 knowledge-base evidence。

### Q8：怎么评估 RAG 效果

答：

> 我用 demo 数据集跑自动评估，主要看 Recall@K、MRR 和 citation_hit_rate。Recall@K 看有没有把正确来源找回来，MRR 看正确来源排得靠不靠前，citation_hit_rate 看最终回答是否引用了正确来源。answer_keyword_hit_rate 只是弱信号，因为措辞可能变化。

### Q9：如果要上生产，你会怎么改

答：

> 我会做几类增强：第一，检索侧接更专业的 sparse index 或 reranker，并用评估集做 ablation；第二，文档处理加任务重试、死信队列和更完整的状态机；第三，权限侧引入组织、团队、审计报表；第四，可观测性接 Prometheus/OpenTelemetry；第五，敏感信息和 prompt injection 做更严格的检测；第六，对 embedding 模型版本做索引隔离和重建策略。

### Q10：当前项目最大的不足是什么

答：

> 当前 BM25 是轻量实现，不是大型倒排索引；metadata route 和 reranker 暂时没有启用；中文分词也只是 bigram 级别。这样做是为了让核心链路简洁、可解释、可测试。后续如果数据规模和质量要求上来，可以引入专业检索引擎、cross-encoder reranker 和更系统的评估集。

## 13. 面试官可能深挖的问题清单

你可以用这些问题自测。

### 13.1 RAG

- Dense retrieval 和 BM25 各解决什么问题？
- 为什么 Dense score 和 BM25 score 不能直接相加？
- RRF 的公式是什么？
- `rrf_k=60` 有什么作用？
- 为什么原始问题权重要高于 sub-query？
- query 拆分什么时候会带来噪声？
- 没有 reranker 的情况下怎么保证排序质量？
- 当前 BM25 和 Elasticsearch/OpenSearch 有什么差别？
- 中文检索怎么处理？
- context compression 会不会破坏引用？
- 引用和实际送给 LLM 的 chunk 如何对齐？

### 13.2 工程

- 为什么上传文件不直接存数据库？
- 为什么用 Celery？
- Worker 失败时怎么处理？
- 文档重复上传怎么判断？
- 删除文档时如何删除向量？
- Qdrant collection 为什么用单 collection？
- Embedding 维度变了怎么办？
- LLM 调用失败怎么处理？
- SSE 中途断开怎么办？
- 如何避免两个请求同时修改同一条记忆？

### 13.3 权限安全

- public/private 知识库区别是什么？
- owner/editor/viewer 怎么控制？
- security_level 怎么过滤？
- Dense 和 BM25 是否都做了权限过滤？
- 用户能否通过提问绕过权限？
- Prompt injection 怎么防？
- 记忆里如果有敏感信息怎么办？

### 13.4 Agent 和记忆

- Supervisor 为什么需要 LLM？
- 为什么还要规则兜底？
- Memory Agent 在回答前后分别做什么？
- pending 记忆为什么不进入上下文？
- 什么时候 merge，什么时候 supersede？
- 记忆召回的阈值怎么设？
- Summary 和 Writing 为什么复用 RAG？

### 13.5 测试和评估

- 你有哪些单元测试？
- 怎么验证权限过滤？
- 怎么验证 RRF？
- 怎么验证记忆状态流转？
- RAG 评估集怎么构造？
- 为什么 answer_keyword_hit_rate 只是弱信号？
- 如果 Recall@K 高但答案差，怎么排查？
- 如果 Recall@K 低，怎么排查？

## 14. 你可以主动展示的亮点

面试里不要只等对方问。你可以主动说这些：

### 14.1 可解释检索

> 我不只是把 topK chunk 扔给模型，还保存了 retrieval log，里面有 query、routes、candidates、RRF 分数和 selected chunks。这样可以定位一次回答到底是召回问题、排序问题还是生成问题。

### 14.2 安全过滤贯穿两路检索

> Dense 和 BM25 都做了 security_level 过滤，不是只在 API 层过滤。这样即使检索结果来自不同存储，也不会泄漏高密级文档。

### 14.3 Agent 不重写 RAG

> Agent 节点复用 `build_rag_answer()`，所以直接问答和 Agent 问答不会出现两套检索逻辑。

### 14.4 记忆不当事实源

> 记忆只影响偏好和上下文理解，制度事实必须来自知识库 chunk。这个边界能避免“用户曾经说过的话”污染企业事实回答。

### 14.5 工程质量闭环

> 项目有 `scripts/check_project.py`，会跑后端单测、Python 编译、迁移验证、前端构建和 docker compose config。不是只靠手工点页面。

## 15. 不要这么说

这些说法容易被追问打穿：

| 不建议说法 | 为什么不好 | 更好的说法 |
|---|---|---|
| “我用了 LangGraph，所以是 Agentic RAG” | 太空，像堆概念 | “Agent 用于受控路由和 trace，RAG 仍复用同一 service” |
| “用了向量数据库，所以检索很准” | 向量检索不是万能 | “Dense 和 BM25 互补，RRF 融合，评估用 Recall@K/MRR” |
| “长期记忆会让回答更智能” | 容易引出记忆污染 | “记忆只处理用户偏好，不作为知识事实依据” |
| “支持权限控制” | 太泛 | “Qdrant payload filter 和 PostgreSQL 查询都按 knowledge_base/security_level 过滤” |
| “支持评估” | 太泛 | “用 expected_sources 计算 Recall@K、MRR、citation_hit_rate” |
| “可以上生产” | 容易被追问规模和运维 | “当前是本地工程闭环，上生产还要补监控、重试、索引版本、专业检索引擎等” |

## 16. 演示顺序

如果面试允许你演示，建议按这个顺序：

1. 打开 `http://localhost:5173`。
2. 登录管理员。
3. 展示知识库列表。
4. 打开知识库详情，展示已 indexed 文档和 chunks。
5. 提问一个制度问题。
6. 展示回答和 citations。
7. 展示检索解释：routes、retrieval_queries、selected_chunks、RRF。
8. 展示 Agent trace。
9. 展示长期记忆页面。
10. 展示管理员指标或 LLM logs。

演示时重点说：

> 这里不是只看回答文本，而是看这次回答用了哪些 chunk、为什么选它们、是否有引用、是否能追踪 LLM 调用和 Agent 路由。

## 17. 复习路线

### 第一天：掌握主链路

读：

- `README.md`
- `docs/rag_pipeline.md`
- `apps/backend/app/services/document_service.py`
- `apps/backend/app/workers/document_tasks.py`
- `apps/backend/app/services/qa_service.py`
- `apps/backend/app/rag/advanced_retrieval.py`

目标：

- 能画出文档入库链路。
- 能画出 RAG 问答链路。
- 能解释 Dense + BM25 + RRF。

### 第二天：掌握 Agent、记忆和权限

读：

- `docs/agent_orchestration.md`
- `docs/prompts.md`
- `apps/backend/app/agents/graph.py`
- `apps/backend/app/agents/supervisor.py`
- `apps/backend/app/agents/rag_agent.py`
- `apps/backend/app/services/memory_service.py`
- `apps/backend/app/services/knowledge_base_service.py`

目标：

- 能解释 Agent 为什么不绕过 RAG。
- 能解释 memory active/pending/superseded/ignored。
- 能解释 public/private 和 security_level。

### 第三天：准备追问和演示

读：

- `docs/evaluation.md`
- `docs/manual_acceptance_checklist.md`
- `apps/backend/tests/test_advanced_retrieval.py`
- `apps/backend/tests/test_security_levels.py`
- `apps/backend/tests/test_memory_service.py`

目标：

- 能回答“怎么测”。
- 能回答“有什么不足”。
- 能讲未来迭代计划。

## 18. 最后的面试心法

这个项目最强的地方不是“用了很多组件”，而是你能把它讲成一个受控系统：

```text
数据怎么进来
权限怎么限制
检索怎么召回
排序怎么融合
上下文怎么组装
模型怎么被约束
答案怎么引用
过程怎么记录
效果怎么评估
失败怎么排查
```

面试里你要不断把话题拉回这条线。  
这样即使面试官深挖某个点，你也不是在背概念，而是在解释一个真实系统的设计。
