# 项目彻底理解路线图

这份文档面向一个目标：不是“看过这个项目”，而是能把它讲清、跑通、定位问题，并能在不破坏边界的前提下改功能。

我的假设：

- 你想理解的是当前仓库的实际实现，不只是 RAG 概念。
- 你会边读边敲一份“学习版”代码；不建议直接在主项目里重写。
- 你最需要知道哪些必须亲手敲，哪些只要读懂即可。

彻底理解的成功标准：

- 能从“用户上传文档”讲到“Qdrant 中出现向量点”。
- 能从“用户提问”讲到“返回 answer、citations、retrieval_log、agent trace”。
- 能解释权限边界：私人、部门、公开知识库分别谁能看、谁能上传、密级如何过滤。
- 能解释 Agent 不是重写 RAG，而是在已有 service 上做受控编排。
- 能跑通测试、冒烟脚本和 RAG 评估，并知道失败时看哪个模块。

## 一句话总图

```text
React 前端
-> FastAPI API
-> service 层做业务编排和权限判断
-> PostgreSQL 保存用户、知识库、文档、chunk、会话、日志
-> MinIO 保存原始上传文件
-> Celery worker 异步解析、切分、embedding、写 Qdrant
-> RAG 检索 Dense + BM25 + RRF
-> LLM 基于知识库上下文回答
-> Agent 在 RAG 上方加载记忆、判断意图、记录 trace
```

## 先跑通，再读代码

第一步不要急着看所有文件。先让项目从外部表现上跑起来。

必做：

```bash
docker compose -f infra/docker-compose.yml --env-file .env.example config
python scripts/check_project.py --skip-frontend --skip-compose
```

如果本地模型 key、Docker 服务都准备好了，再跑：

```bash
docker compose -f infra/docker-compose.yml --env-file .env.example up --build
python scripts/smoke_demo.py
```

要观察的结果：

- `/api/health` 表示 FastAPI 活着。
- `/api/ready` 表示 database、redis、qdrant、minio 都可用。
- 上传 `demo/company_policy_demo.md` 后，文档状态应从 `uploaded` 到 `indexed`。
- 问“住宿报销上限是多少？”时，答案应带 citations。

## 读项目的推荐顺序

### 0. 项目地图

先读这些：

```text
README.md
docs/rag_pipeline.md
docs/agent_orchestration.md
docs/manual_acceptance_checklist.md
infra/docker-compose.yml
.env.example
```

你要先建立五个问题的答案：

- 服务怎么启动？
- 原始文件存哪里？
- chunk 和业务数据存哪里？
- 向量存哪里？
- 权限和密级在哪里过滤？

### 1. 后端最小骨架

按顺序读：

```text
apps/backend/pyproject.toml
apps/backend/app/core/config.py
apps/backend/app/main.py
apps/backend/app/api/router.py
apps/backend/app/core/health.py
apps/backend/app/db/session.py
```

重点理解：

- `create_app()` 如何挂 CORS 和总路由。
- `lifespan()` 为什么启动时执行 `init_db()` 和 `ensure_qdrant_collection()`。
- `Settings` 如何从 `.env` 读配置。
- 生产环境为什么拒绝默认 JWT secret、SQLite、`AUTO_CREATE_TABLES=true`。

验证：

```bash
cd apps/backend
uvicorn app.main:app --reload
```

### 2. 鉴权与当前用户

核心文件：

```text
apps/backend/app/db/models/user.py
apps/backend/app/db/models/refresh_token.py
apps/backend/app/core/security.py
apps/backend/app/schemas/auth.py
apps/backend/app/services/auth_service.py
apps/backend/app/api/deps.py
apps/backend/app/api/routes/auth.py
```

必须理解：

- 第一个注册用户为什么自动变成管理员和 L5。
- 密码只存 hash，refresh token 也只存 hash。
- 受保护接口如何通过 `get_current_user()` 拿用户。
- refresh token 为什么要轮换和撤销。

验证：

```text
POST /api/auth/register
POST /api/auth/login
GET /api/me
POST /api/auth/refresh
POST /api/auth/logout
```

### 3. 企业权限边界

核心文件：

```text
apps/backend/app/db/models/department.py
apps/backend/app/db/models/knowledge_base.py
apps/backend/app/core/security_levels.py
apps/backend/app/services/department_service.py
apps/backend/app/services/knowledge_base_service.py
apps/backend/app/api/routes/knowledge_bases.py
apps/backend/app/api/routes/departments.py
```

重点函数：

```text
ensure_kb_access()
resolve_search_scope()
has_implicit_view_access()
effective_role()
resolve_department_id_for_write()
```

必须理解：

- 私人知识库靠成员关系隔离。
- 部门知识库对同部门用户可读，维护需要 owner/editor 或管理员。
- 公开知识库对登录用户可读，但文档维护只允许管理员。
- 公开/部门知识库检索时使用 `security_level <= 当前用户清级`。
- 无权访问私人资源时很多地方返回 404，是为了不暴露资源存在。

### 4. 文档上传与入库

核心文件：

```text
apps/backend/app/db/models/document.py
apps/backend/app/storage/minio_client.py
apps/backend/app/services/document_service.py
apps/backend/app/workers/celery_app.py
apps/backend/app/workers/document_tasks.py
apps/backend/app/rag/loaders.py
apps/backend/app/rag/splitters.py
```

入库链路：

```text
upload API
-> document_service 校验权限、文件类型、大小、hash 去重
-> MinIO 保存原文
-> PostgreSQL 创建 documents
-> Celery 投递 process_document
-> worker 下载原文
-> loaders 解析 PDF/DOCX/TXT/MD/CSV
-> splitters 切 chunk
-> document_chunks 入库
-> embedding
-> Qdrant upsert
-> document.status = indexed
```

必须理解：

- HTTP 上传接口不直接做耗时解析。
- 文档状态是前后端协作的关键协议。
- 同一知识库内用 `content_hash` 去重。
- 删除文档必须同时清理 Qdrant 向量和 MinIO 对象，失败时不提交删除。

### 5. Embedding、Qdrant 与检索

核心文件：

```text
apps/backend/app/rag/embeddings.py
apps/backend/app/rag/vector_store.py
apps/backend/app/rag/retrieval.py
apps/backend/app/rag/advanced_retrieval.py
```

必须理解：

- 当前只支持 OpenAI-compatible embedding provider，没有本地兜底。
- 项目使用一个 Qdrant collection：`knowledge_chunks`。
- Qdrant payload 至少包含 `user_id`、`knowledge_base_id`、`document_id`、`chunk_id`、`security_level`。
- Dense retrieval 先走 Qdrant，再回 PostgreSQL hydrate 和二次过滤。
- BM25 使用数据库里的 chunk 正文做词项召回。
- RRF 按 chunk id 融合多路候选。
- context compression 只压缩给 LLM 的上下文，不改变原始 chunk。

重点函数：

```text
retrieve_advanced_chunks()
plan_retrieval_queries()
retrieve_dense_routes()
retrieve_bm25_routes()
fuse_candidates()
compress_selected_chunks()
```

### 6. RAG 回答与日志

核心文件：

```text
apps/backend/app/rag/answering.py
apps/backend/app/llm/provider.py
apps/backend/app/services/qa_service.py
apps/backend/app/services/retrieval_log_service.py
apps/backend/app/services/llm_log_service.py
apps/backend/app/api/routes/qa.py
```

必须理解：

- `qa_service.build_rag_answer()` 是直接问答和 Agent 问答共同复用的核心。
- `answering.py` 负责把 selected chunks 组装成带 `[1]` 编号的上下文。
- `provider.py` 的系统提示明确要求：企业事实只能来自 Knowledge context。
- 记忆只能影响风格或回答用户记忆问题，不能当企业知识依据。
- 每次问答都应该留下 retrieval log、LLM log 和 audit log。

### 7. 会话与 SSE

核心文件：

```text
apps/backend/app/db/models/conversation.py
apps/backend/app/services/conversation_service.py
apps/backend/app/api/routes/conversations.py
apps/frontend/src/lib/api.ts
apps/frontend/src/pages/ChatPage.tsx
```

必须理解：

- 会话保存 `search_scope`，不是每次前端随便传。
- 用户消息先入库，assistant 消息在 Agent run 完成后入库。
- SSE 事件包括 `conversation`、`user_message`、`trace`、`token`、`retrieval_log`、`agent_run`、`citations`、`assistant_message`、`done`、`error`。
- 前端 `streamConversationMessage()` 用 `ReadableStream` 解析 SSE，不是浏览器原生 `EventSource`。

### 8. Agent 编排

核心文件：

```text
apps/backend/app/agents/state.py
apps/backend/app/agents/graph.py
apps/backend/app/agents/supervisor.py
apps/backend/app/agents/rag_agent.py
apps/backend/app/agents/memory_agent.py
apps/backend/app/agents/summary_agent.py
apps/backend/app/agents/writing_agent.py
apps/backend/app/services/agent_service.py
```

必须理解：

- `AgentGraphState` 是一次 Agent run 的状态容器。
- `graph.py` 支持 `langgraph` 和 `sequential` 两种执行方式。
- Supervisor 只判断 intent，不直接回答。
- RAG Agent 复用 `qa_service.build_rag_answer()`，不重写检索。
- Summary/Writing 也先检索依据，再让 LLM 生成结果。
- trace 是解释 Agent 行为的核心产物。

### 9. 记忆系统

核心文件：

```text
apps/backend/app/db/models/user_memory.py
apps/backend/app/services/memory_service.py
apps/backend/app/agents/memory_agent.py
apps/backend/app/api/routes/memories.py
apps/frontend/src/pages/MemoriesPage.tsx
```

必须理解：

- Redis 保存短期记忆，PostgreSQL 保存长期记忆。
- 长期记忆状态包括 `active`、`pending`、`superseded`、`ignored`。
- 自动记忆不是“什么都记”，而是保守提取稳定偏好、角色、项目和长期指令。
- `touch`、`merge`、`supersede` 是防止记忆重复和冲突的关键。
- 记忆不能绕过知识库权限，也不能替代企业事实依据。

### 10. 前端产品闭环

核心文件：

```text
apps/frontend/src/App.tsx
apps/frontend/src/lib/api.ts
apps/frontend/src/stores/authStore.ts
apps/frontend/src/pages/LoginPage.tsx
apps/frontend/src/pages/RegisterPage.tsx
apps/frontend/src/pages/KnowledgeBaseListPage.tsx
apps/frontend/src/pages/KnowledgeBaseDetailPage.tsx
apps/frontend/src/pages/ChatPage.tsx
apps/frontend/src/pages/MemoriesPage.tsx
apps/frontend/src/pages/AdminMetricsPage.tsx
```

重点理解：

- `App.tsx` 负责登录态恢复、refresh token 和路由保护。
- `api.ts` 是前端和后端通信的唯一集中入口。
- `KnowledgeBaseDetailPage` 串起上传、文档状态、chunk 查看、非流式问答。
- `ChatPage` 串起会话、SSE token、citations、retrieval logs、agent runs、LLM logs、反馈和记忆。
- `/admin` 只对管理员开放，用于部门、用户清级、指标和审计。

### 11. 测试、脚本与验收

核心文件：

```text
apps/backend/tests/*
scripts/check_project.py
scripts/smoke_demo.py
scripts/verify_migrations.py
scripts/evaluate_rag.py
scripts/run_eval.py
docs/manual_acceptance_checklist.md
docs/evaluation.md
```

必须理解：

- `tests/helpers.py` 用 SQLite 内存库隔离测试。
- 权限、文档、检索、记忆、Agent、流式会话都有对应测试。
- `check_project.py` 是质量门禁。
- `smoke_demo.py` 是端到端主链路。
- `run_eval.py` 是 RAG 引用质量的可重复弱信号。

## 必须手敲一遍的内容

下面这些不是因为代码最难，而是因为它们承载项目边界。只读很容易以为懂了，亲手敲过才能真正知道为什么这么分层。

### A. 后端最小闭环

必须手敲：

```text
core/config.py
main.py
api/router.py
api/routes/health.py
db/session.py 的核心结构
```

你要亲手敲出一个能启动、能读配置、能返回 `/health` 的 FastAPI 应用。

### B. 鉴权闭环

必须手敲：

```text
db/models/user.py
db/models/refresh_token.py
core/security.py 的 hash/JWT/refresh token 函数
schemas/auth.py
services/auth_service.py
api/deps.py
api/routes/auth.py
```

验证目标：

```text
注册 -> 登录 -> 带 Bearer token 访问 /me -> refresh -> logout 后 refresh 失效
```

### C. 知识库权限闭环

必须手敲：

```text
db/models/department.py
db/models/knowledge_base.py
services/knowledge_base_service.py 中的权限核心函数
api/routes/knowledge_bases.py
```

尤其要手敲：

```text
ensure_kb_access()
resolve_search_scope()
has_implicit_view_access()
effective_role()
```

验证目标：

- A 用户不能看到 B 用户私人知识库。
- 普通用户不能创建公开知识库。
- 同部门用户可以读部门知识库。
- 公开/部门知识库按用户清级过滤。

### D. 文档上传与 worker 入库闭环

必须手敲：

```text
services/document_service.py 的上传、去重、权限和删除清理
workers/celery_app.py
workers/document_tasks.py
rag/loaders.py
rag/splitters.py
storage/minio_client.py 的主要封装
```

验证目标：

```text
上传 demo/company_policy_demo.md
-> documents.status = uploaded
-> worker 处理
-> document_chunks 有数据
-> documents.status = indexed
```

### E. RAG 检索核心

必须手敲：

```text
rag/embeddings.py 的 provider 接口
rag/vector_store.py 的 collection、payload、upsert、search
rag/retrieval.py
rag/advanced_retrieval.py 的 query planning、dense、BM25、RRF、compression
```

最值得手敲的函数：

```text
plan_retrieval_queries()
retrieve_dense_routes()
retrieve_bm25_routes()
fuse_candidates()
compress_selected_chunks()
```

验证目标：

- 能解释每条 retrieval route。
- 能从 retrieval log 里看出 candidates 和 selected chunks。
- 能说清为什么最终 chunk 被选中。

### F. RAG 回答编排

必须手敲：

```text
rag/answering.py
llm/provider.py 的 build_answer_messages()
services/qa_service.py
services/retrieval_log_service.py
services/llm_log_service.py
```

验证目标：

```text
POST /api/knowledge-bases/{kb_id}/ask
-> answer
-> citations
-> retrieval_log
-> LLM 调用落库，可通过日志接口查看
```

### G. SSE 会话核心

必须手敲：

```text
db/models/conversation.py
services/conversation_service.py 的 stream_message_response()
frontend/src/lib/api.ts 的 streamConversationMessage()
frontend/src/pages/ChatPage.tsx 中处理 token/citations/log/agent_run 的状态逻辑
```

注意：`ChatPage.tsx` 的 JSX 不需要逐字全抄，但 SSE 状态机必须亲手实现一遍。

### H. Agent 编排核心

必须手敲：

```text
agents/state.py
agents/graph.py
agents/supervisor.py
agents/rag_agent.py
agents/memory_agent.py
services/agent_service.py
```

验证目标：

- 普通制度问题走 RAG。
- “请总结”走 Summary。
- “请写一份”走 Writing。
- “你记得我什么”走 Memory answer。
- 每次 run 都能看到 trace。

### I. 记忆系统核心

必须手敲：

```text
db/models/user_memory.py
services/memory_service.py 的短期记忆、长期记忆、去重、merge、supersede
api/routes/memories.py
```

最值得手敲的函数：

```text
process_user_memory()
process_memory_operation()
upsert_memory_candidate()
retrieve_relevant_memories()
build_memory_context_for_question()
```

验证目标：

```text
I prefer concise answers -> create
I prefer concise answers -> touch
I prefer detailed answers -> supersede
```

### J. 测试与脚本

必须手敲：

```text
tests/helpers.py
至少一组鉴权测试
至少一组知识库权限测试
至少一组 document_service 测试
至少一组 advanced_retrieval 测试
至少一组 memory_service 测试
scripts/smoke_demo.py 的简化版
```

验证目标：

```bash
$env:PYTHONPATH="apps/backend"
python -m unittest discover -s apps/backend/tests
```

## 建议手敲，但不必逐字敲完

这些内容能加深理解，但逐字抄完整收益不高。

```text
所有 schemas/*
所有 db/models/* 的字段
services/admin_service.py
services/feedback_service.py
services/audit_service.py
frontend/src/pages/KnowledgeBaseListPage.tsx
frontend/src/pages/KnowledgeBaseDetailPage.tsx
frontend/src/pages/MemoriesPage.tsx
frontend/src/pages/AdminMetricsPage.tsx
scripts/check_project.py
scripts/evaluate_rag.py
```

建议做法：

- 每类 schema 亲手写 1-2 个代表。
- 每类 SQLAlchemy model 亲手写关键字段和 relationship。
- 前端页面只手敲状态、请求、错误处理和关键交互；布局可以读懂即可。
- 管理员指标和反馈服务只要能解释数据从哪些表聚合出来。

## 只读即可的内容

这些文件更多是样板、配置或展示材料，理解即可，不建议投入大量手敲时间。

```text
apps/backend/alembic/script.py.mako
大部分 alembic/versions/*
apps/backend/Dockerfile
apps/frontend/Dockerfile
apps/frontend/tsconfig*.json
apps/frontend/vite.config.ts
apps/frontend/src/styles/globals.css
docs/resume_project.md
docs/interview_prep_guide.md
docs/architecture_diagrams.md
plan/VIBE_CODING_IMPLEMENTATION_PLAN.md
```

例外：如果你要专门学数据库迁移，就挑一两个 Alembic 版本手敲；否则只要知道 migration 如何覆盖当前模型即可。

## 推荐三遍学习法

### 第一遍：MVP 主链路

目标：理解企业文档如何被入库、检索、引用回答。

范围：

```text
启动配置
-> 鉴权
-> 知识库权限
-> 文档上传
-> worker 入库
-> embedding/Qdrant
-> 非流式 RAG 问答
```

完成标准：

```text
你能不用前端，只用 API 跑通上传、indexed、ask、citations。
```

### 第二遍：产品闭环

目标：理解真实用户如何使用它。

范围：

```text
前端登录态
-> 知识库页面
-> 文档状态刷新
-> ChatPage
-> SSE
-> citations/retrieval log/agent trace 展示
```

完成标准：

```text
刷新页面后历史会话还在，流式回答能逐段显示，引用和 trace 能被展开查看。
```

### 第三遍：Agentic 能力和工程化

目标：理解它为什么不只是普通 RAG demo。

范围：

```text
Agent graph
-> supervisor
-> memory
-> summary/writing
-> feedback/admin metrics/audit
-> tests/check_project/smoke/eval
```

完成标准：

```text
你能解释 RAG、Agent、Memory、日志、评估之间的边界，并能根据 retrieval log 排查一次回答质量问题。
```

## 每一步都要问自己的问题

读或手敲任何一个文件时，都回答这 5 个问题：

1. 这个文件属于哪一层：route、service、model、schema、rag、agent、worker、frontend？
2. 它的上游是谁？下游是谁？
3. 它有没有做越界的事？
4. 它失败时用户会看到什么？
5. 我怎么最小化验证它真的工作？

## 最小练习任务

如果你想证明自己真的理解了，做这些练习：

1. 给 `advanced_retrieval.py` 增加一个 retrieval log 字段，记录每条 route 的候选数量，并补测试。
2. 给上传文档增加一个新的文本格式，例如 `.json`，只支持提取字符串值，并补 loaders/splitters 测试。
3. 给前端 ChatPage 的引用面板增加 `security_level` 显示逻辑，并确认私人知识库不误导用户。
4. 写一个失败排查脚本：输入 document_id，输出文档状态、chunk_count、最近错误和是否存在 Qdrant point。

这些任务不大，但会迫使你穿过配置、service、RAG、测试和前端边界。

## 不要这样学

- 不要从前端页面 JSX 开始逐字抄。
- 不要先背所有数据库字段。
- 不要跳过权限测试直接看 RAG。
- 不要把 Agent 当成“更高级的聊天逻辑”独立理解，它复用的是 RAG service。
- 不要把 memory 当成企业事实来源。
- 不要只看 README，不跑 `smoke_demo.py` 或手工主链路。

## 最后验收清单

当你认为自己理解透了，闭卷回答这些问题：

- 用户注册后 token 怎么签发、怎么刷新、怎么失效？
- `ensure_kb_access()` 为什么是项目里最关键的函数之一？
- 上传文档失败时哪些资源可能需要清理？
- 为什么 Qdrant payload 里必须有 `security_level`？
- Dense 和 BM25 的候选如何被 RRF 融合？
- 为什么 `qa_service.build_rag_answer()` 要被 Agent 复用？
- SSE token 是怎么从后端线程传到前端页面的？
- Memory 的 `touch`、`merge`、`supersede` 分别解决什么问题？
- retrieval log、LLM log、agent run、audit log 分别看什么？
- 如果“没有引用”，你会按什么顺序排查？

能回答这些问题，并能用命令跑通质量门禁和冒烟链路，才算真正理解这个项目。