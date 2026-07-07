# 手敲项目的实际开发顺序

这份文档是给“想自己从 0 敲一遍这个项目”的学习路线。

不要按目录字母顺序敲，也不要一上来就从前端页面开始。真实开发更像一层层把闭环长出来：

```text
能启动
-> 能登录
-> 能建知识库
-> 能上传文档
-> 能解析入库
-> 能检索
-> 能回答
-> 能流式对话
-> 能编排 Agent
-> 能记忆
-> 能观测和验收
-> 最后补前端体验、测试和文档
```

你的项目现在是一个比较典型的企业级 Agentic RAG 单体仓库：

- `apps/backend`：FastAPI 后端，负责鉴权、权限、文档、RAG、Agent、记忆、日志和管理指标。
- `apps/frontend`：React/Vite 前端，负责登录、知识库、文档上传、聊天、记忆和管理页面。
- `infra`：本地 Docker Compose，提供 PostgreSQL、Redis、Qdrant、MinIO、backend、worker、frontend。
- `scripts`：质量门禁、迁移校验、冒烟演示、RAG 评估。
- `docs`：架构、API、RAG、Agent、验收和面试讲解材料。
- `demo`：演示文档和评估问题。

如果你要学习理解，核心不是“把所有文件抄完”，而是每一步都能回答三个问题：

1. 这一层解决什么问题？
2. 它依赖前面哪一层？
3. 我怎么验证它真的能工作？

## 总体节奏

每完成一个业务模块，都按这个小循环走：

```text
先写模型和 schema
-> 再写 service
-> 再写 route
-> 挂到 router.py
-> 写最小测试或手工请求
-> 确认能运行
-> 再进入下一层
```

不要先把所有数据库模型都写完，也不要先把所有页面画完。企业级项目最怕“代码很多，但没有任何一条业务链路真的跑通”。

## 0. 先读懂目标，而不是先写代码

先看这些文件：

```text
README.md
docs/architecture.md
docs/rag_pipeline.md
docs/agent_orchestration.md
docs/manual_acceptance_checklist.md
```

这一阶段的作用：

你要先知道项目最终要跑通哪条主链路。本项目主链路是：

```text
注册/登录
-> 创建知识库
-> 上传企业制度文档
-> 后台解析、切分、向量化
-> 用户提问
-> 按权限检索 chunk
-> LLM 基于知识库上下文回答
-> 返回引用、检索日志、Agent trace
-> 前端展示
```

这一阶段先不要写代码。你只需要在脑子里建立项目地图：

- 用户是谁？
- 文档放在哪里？
- chunk 放在哪里？
- 向量放在哪里？
- 权限在哪里判断？
- LLM 在哪里调用？
- 前端怎么拿到流式回答？

验证标准：

你能用自己的话讲清楚“上传一份文档到得到带引用回答”的完整链路。

## 1. 先写项目启动骨架

建议先敲：

```text
README.md
.env.example
infra/docker-compose.yml
apps/backend/Dockerfile
apps/frontend/Dockerfile
```

这一阶段的作用：

企业项目第一步不是业务代码，而是先定义“系统怎么跑起来”。你的项目依赖多个外部服务：

- PostgreSQL：关系数据，保存用户、知识库、文档、会话、日志等。
- Redis：Celery 队列和短期记忆。
- Qdrant：向量检索。
- MinIO：原始文件对象存储。
- Backend：FastAPI API。
- Worker：异步处理文档。
- Frontend：React 页面。

你在这一阶段要理解：后面的每个模块都不是孤立文件，它会依赖这些基础服务。

重点：

- `.env.example` 写清所有环境变量，不要只写能启动的最少配置。
- `docker-compose.yml` 先把服务关系搭起来。
- backend 和 worker 复用同一份后端代码，但启动命令不同。
- 先用 `.env.example` 保证别人能复制出同样环境。

验证：

```bash
docker compose -f infra/docker-compose.yml --env-file .env.example config
```

此时只验证 Compose 配置能解析，不要求业务接口已经可用。

## 2. 搭 FastAPI 最小入口

建议顺序：

```text
apps/backend/pyproject.toml
apps/backend/app/__init__.py
apps/backend/app/core/config.py
apps/backend/app/core/health.py
apps/backend/app/api/__init__.py
apps/backend/app/api/routes/health.py
apps/backend/app/api/router.py
apps/backend/app/main.py
```

这一阶段的作用：

先让后端成为一个能启动、能读配置、能响应健康检查的服务。

这里先不要写用户、知识库、RAG。原因很简单：如果应用入口、配置、路由挂载和健康检查都不稳定，后面业务越写越难排查。

每个文件的意义：

- `pyproject.toml`：声明后端依赖和 Python 包结构。
- `config.py`：集中读取环境变量，并做生产环境安全校验。
- `health.py`：封装健康检查逻辑。
- `routes/health.py`：把健康检查暴露成 HTTP API。
- `router.py`：统一汇总所有 API router。
- `main.py`：创建 FastAPI app，挂 CORS、生命周期和总路由。

验证：

```bash
cd apps/backend
uvicorn app.main:app --reload
```

访问：

```text
http://localhost:8000/api/health
http://localhost:8000/api/ready
```

阶段完成标准：

后端能启动，健康检查接口能返回，配置读取错误能被明确暴露。

## 3. 建数据库基础设施

建议顺序：

```text
apps/backend/app/db/__init__.py
apps/backend/app/db/base.py
apps/backend/app/db/session.py
apps/backend/app/db/runtime_schema.py
apps/backend/alembic.ini
apps/backend/alembic/env.py
apps/backend/alembic/script.py.mako
```

这一阶段的作用：

建立统一的数据库入口。真实项目里不要到处创建数据库连接，所有 API、service、worker 都应该通过统一的 session 进入数据库。

每个文件的意义：

- `base.py`：定义 SQLAlchemy 的 `Base`。
- `session.py`：定义 `engine`、`SessionLocal`、`init_db()`、`get_db()`。
- `runtime_schema.py`：开发期兼容旧本地库的运行时 schema 补齐逻辑。
- `alembic/env.py`：让 Alembic 知道模型元数据和数据库地址。
- `alembic/versions/*`：正式的数据库迁移脚本。

学习重点：

- 开发环境可以 `AUTO_CREATE_TABLES=true`，方便快速演示。
- 生产环境应该 `AUTO_CREATE_TABLES=false`，使用 Alembic 迁移。
- API 层不直接管理事务细节，service 层拿到 `Session` 后处理业务。

验证：

```bash
python scripts/verify_migrations.py
```

阶段完成标准：

迁移后的数据库结构和 SQLAlchemy 模型一致。

## 4. 先做用户、鉴权和当前用户

建议顺序：

```text
apps/backend/app/db/models/user.py
apps/backend/app/db/models/refresh_token.py
apps/backend/app/schemas/auth.py
apps/backend/app/core/security.py
apps/backend/app/services/auth_service.py
apps/backend/app/api/deps.py
apps/backend/app/api/routes/auth.py
apps/backend/app/api/router.py
```

这一阶段的作用：

这是第一个真正的业务闭环。后面所有知识库、文档、问答、记忆都依赖“当前用户是谁”。

每个文件的意义：

- `user.py`：保存邮箱、用户名、密码哈希、是否管理员、密级、部门等。
- `refresh_token.py`：保存 refresh token 哈希，用于刷新和撤销。
- `schemas/auth.py`：定义注册、登录、刷新 token 的请求响应结构。
- `security.py`：密码哈希、密码校验、JWT 创建和解析。
- `auth_service.py`：注册、登录、刷新、退出的业务逻辑。
- `api/deps.py`：提供 `get_current_user()` 和 `require_admin()`。
- `routes/auth.py`：暴露 `/auth/register`、`/auth/login`、`/me` 等接口。

业务规则：

- 第一个注册用户自动成为管理员。
- 后续用户默认普通权限。
- 密码只存 hash，不存明文。
- refresh token 只存 hash。
- 所有受保护接口都复用 `get_current_user()`。

验证：

```text
POST /api/auth/register
POST /api/auth/login
GET /api/me
POST /api/auth/refresh
POST /api/auth/logout
```

阶段完成标准：

你能注册用户、登录拿 token，并用 token 访问 `/api/me`。

## 5. 做企业权限边界：部门、知识库、密级

建议顺序：

```text
apps/backend/app/db/models/department.py
apps/backend/app/db/models/knowledge_base.py
apps/backend/app/schemas/department.py
apps/backend/app/schemas/knowledge_base.py
apps/backend/app/core/security_levels.py
apps/backend/app/services/department_service.py
apps/backend/app/services/knowledge_base_service.py
apps/backend/app/api/routes/departments.py
apps/backend/app/api/routes/knowledge_bases.py
apps/backend/app/api/router.py
```

这一阶段的作用：

RAG 项目的安全边界不能最后补。你要先定义：谁能看到哪些知识库，谁能编辑，谁能检索哪些文档。

核心概念：

- `Department`：部门。
- `KnowledgeBase`：知识库，有 `private`、`department`、`public` 三种可见性。
- `KnowledgeBaseMember`：知识库成员角色，包含 owner、editor、viewer。
- `security_level`：用户和文档都有密级。
- `ensure_kb_access()`：后面所有文档和问答权限检查都复用它。
- `resolve_search_scope()`：决定问答时检索单个库、部门库，还是所有可访问库。

学习重点：

- API route 不应该自己判断复杂权限。
- 权限逻辑集中在 service 层，方便复用和测试。
- 对无权访问的资源，很多时候返回 404 比 403 更安全，避免暴露资源存在性。

验证：

```text
POST /api/departments
GET /api/departments
POST /api/knowledge-bases
GET /api/knowledge-bases
GET /api/knowledge-bases/{id}
PATCH /api/knowledge-bases/{id}
DELETE /api/knowledge-bases/{id}
```

阶段完成标准：

普通用户只能看到自己有权看的知识库，管理员可以管理公开或部门范围的知识库。

## 6. 做对象存储和文档上传记录

建议顺序：

```text
apps/backend/app/storage/__init__.py
apps/backend/app/storage/minio_client.py
apps/backend/app/db/models/document.py
apps/backend/app/schemas/document.py
apps/backend/app/services/audit_service.py
apps/backend/app/services/document_service.py
apps/backend/app/api/routes/documents.py
apps/backend/app/api/router.py
```

这一阶段的作用：

先完成“文件上传后有地方放，并且数据库有记录”。这一阶段还不急着解析文档，也不急着向量化。

每个文件的意义：

- `minio_client.py`：封装上传、下载、删除对象。
- `document.py`：保存文档和 chunk 表结构。
- `schemas/document.py`：定义上传响应、文档列表、chunk 读取结构。
- `audit_service.py`：记录关键安全动作。
- `document_service.py`：处理上传、去重、删除、权限、审计。
- `routes/documents.py`：暴露上传、列表、详情、删除接口。

业务规则：

- 原始文件进入 MinIO，不直接塞进 PostgreSQL。
- 数据库保存 `object_key`、`content_hash`、`status`、`security_level`。
- 同一知识库内相同内容不重复上传。
- 私有知识库文档默认普通密级。
- 公开/部门知识库需要按用户密级控制可见范围。

验证：

```text
POST /api/knowledge-bases/{kb_id}/documents
GET /api/knowledge-bases/{kb_id}/documents
GET /api/documents/{document_id}
DELETE /api/documents/{document_id}
```

阶段完成标准：

上传后能在 MinIO 找到原始文件，数据库中能看到文档记录，重复上传会被拒绝。

## 7. 加 Celery Worker，把文档变成 chunk

建议顺序：

```text
apps/backend/app/workers/__init__.py
apps/backend/app/workers/celery_app.py
apps/backend/app/rag/loaders.py
apps/backend/app/rag/splitters.py
apps/backend/app/workers/document_tasks.py
apps/backend/app/services/document_service.py
```

这一阶段的作用：

把“上传文档”升级成“文档可入库”。解析、切分、向量化都是耗时任务，不能阻塞 HTTP 请求，所以放到 worker。

每个文件的意义：

- `celery_app.py`：连接 Redis，定义 Celery app。
- `loaders.py`：解析 PDF、DOCX、TXT、Markdown、CSV。
- `splitters.py`：把解析结果切成适合检索的 chunk。
- `document_tasks.py`：控制文档状态流转和入库过程。
- `document_service.py`：上传成功后投递 worker 任务。

文档状态流转：

```text
uploaded
-> parsing
-> chunking
-> embedding
-> indexed
```

失败时：

```text
failed + error_message
```

学习重点：

- HTTP API 只负责接收请求和投递任务。
- worker 负责耗时处理。
- 文档状态是前后端协作的关键，不要只靠日志判断是否完成。

验证：

上传：

```text
demo/company_policy_demo.md
```

观察文档状态是否能从 `uploaded` 走到 `indexed`，并且：

```text
chunk_count > 0
```

## 8. 接 Embedding 和 Qdrant 向量库

建议顺序：

```text
apps/backend/app/rag/embeddings.py
apps/backend/app/rag/vector_store.py
apps/backend/app/rag/retrieval.py
apps/backend/app/workers/document_tasks.py
apps/backend/app/main.py
```

这一阶段的作用：

chunk 只有文本还不够，RAG 需要可检索。这里把 chunk 转成向量并写入 Qdrant。

每个文件的意义：

- `embeddings.py`：封装 OpenAI-compatible embedding provider。
- `vector_store.py`：创建 collection、建 payload index、upsert、search、delete。
- `retrieval.py`：定义基础 dense retrieval 和 `RetrievedChunk`。
- `document_tasks.py`：在 worker 中调用 embedding 和向量 upsert。
- `main.py`：启动时确保 Qdrant collection 存在。

学习重点：

- Qdrant 只存向量和 payload，不替代关系数据库。
- PostgreSQL 仍然保存文档、chunk、权限、状态等权威数据。
- 检索时必须带 `knowledge_base_id`、`document_id`、`chunk_id`、`security_level` 等 payload。

验证：

上传文档后：

```text
document.status == indexed
document.chunk_count > 0
Qdrant collection 中存在对应 points
```

## 9. 先做非流式 RAG 问答

建议顺序：

```text
apps/backend/app/schemas/qa.py
apps/backend/app/services/retrieval_log_service.py
apps/backend/app/services/llm_log_service.py
apps/backend/app/rag/advanced_retrieval.py
apps/backend/app/rag/answering.py
apps/backend/app/llm/provider.py
apps/backend/app/services/qa_service.py
apps/backend/app/api/routes/qa.py
apps/backend/app/api/router.py
```

这一阶段的作用：

建立项目核心能力：用户提问，系统按权限检索 chunk，用 LLM 基于知识库上下文回答，并返回引用。

每个文件的意义：

- `schemas/qa.py`：定义提问请求和回答响应。
- `retrieval_log_service.py`：保存检索过程、候选 chunk、最终 chunk。
- `llm_log_service.py`：保存模型调用 token、延迟、错误等。
- `advanced_retrieval.py`：query planning、Dense、BM25、RRF、上下文压缩。
- `answering.py`：把 selected chunks 格式化成带 `[1]`、`[2]` 的上下文。
- `provider.py`：封装 LLM 调用、意图分类、记忆抽取、流式 token。
- `qa_service.py`：串起权限、检索、日志、LLM、引用。
- `routes/qa.py`：暴露 `/knowledge-bases/{kb_id}/ask`。

学习重点：

- RAG 回答必须有 citations，不能只返回一段自然语言。
- 检索日志是调试 RAG 的生命线。
- LLM provider 只负责调用模型，不应该懂业务权限。
- `qa_service.py` 才是问答业务编排层。

验证：

```text
POST /api/knowledge-bases/{kb_id}/ask
```

示例问题：

```text
住宿报销上限是多少？
```

阶段完成标准：

响应里包含：

- `answer`
- `citations`
- `retrieval_log`

## 10. 做会话和 SSE 流式对话

建议顺序：

```text
apps/backend/app/db/models/conversation.py
apps/backend/app/schemas/conversation.py
apps/backend/app/services/conversation_service.py
apps/backend/app/api/routes/conversations.py
apps/backend/app/api/router.py
```

这一阶段的作用：

非流式问答适合验证 RAG，但真实产品需要持续会话、历史消息和流式输出。

每个文件的意义：

- `conversation.py`：保存会话和消息。
- `schemas/conversation.py`：定义会话、消息、流式请求响应结构。
- `conversation_service.py`：创建会话、保存消息、运行 Agent、生成 SSE 事件。
- `routes/conversations.py`：暴露会话和流式消息接口。

流式事件包含：

```text
conversation
user_message
trace
token
citations
retrieval_log
agent_run
assistant_message
done
error
```

学习重点：

- 流式接口本质上是后端边算边发事件。
- 用户消息要先保存，助手消息在模型完成后保存。
- 失败也要保存 failed message，方便前端和日志追踪。

验证：

```text
POST /api/conversations
POST /api/conversations/{conversation_id}/messages/stream
GET /api/conversations/{conversation_id}/messages
```

阶段完成标准：

不用前端，也能用 API 工具看到 SSE 事件流。

## 11. 加 Agent 编排

建议顺序：

```text
apps/backend/app/db/models/agent_run.py
apps/backend/app/schemas/agent.py
apps/backend/app/agents/state.py
apps/backend/app/agents/tools.py
apps/backend/app/agents/memory_agent.py
apps/backend/app/agents/supervisor.py
apps/backend/app/agents/rag_agent.py
apps/backend/app/agents/summary_agent.py
apps/backend/app/agents/writing_agent.py
apps/backend/app/agents/graph.py
apps/backend/app/services/agent_service.py
apps/backend/app/api/routes/agents.py
apps/backend/app/api/router.py
```

这一阶段的作用：

Agent 不是重写 RAG，而是在 RAG 上面加一层受控编排：

```text
加载记忆
-> 判断意图
-> 选择 RAG / Memory / Chat / Summary / Writing
-> 生成回答
-> 更新记忆
-> 记录 trace
```

每个文件的意义：

- `agent_run.py`：保存一次 Agent 运行结果。
- `schemas/agent.py`：定义 Agent 请求、响应、trace。
- `state.py`：定义一次 Agent 运行的状态对象。
- `supervisor.py`：只判断 intent，不直接回答。
- `rag_agent.py`：复用 `qa_service.build_rag_answer()`。
- `memory_agent.py`：加载和更新用户记忆。
- `summary_agent.py`：处理总结类请求。
- `writing_agent.py`：处理写作类请求。
- `graph.py`：支持 LangGraph，也支持 sequential 调试。
- `agent_service.py`：执行图并保存 `agent_runs`。

学习重点：

- Agent 节点要复用 service，不要重新实现业务权限。
- trace 是调试 Agent 的关键。
- sequential 后端适合本地学习和调试，LangGraph 适合展示更正式的编排。

验证：

```text
POST /api/agent-runs
GET /api/agent-runs
GET /api/agent-runs/{run_id}
```

阶段完成标准：

你能看到一次 Agent run 的 intent、answer、citations、trace。

## 12. 加长期记忆系统

建议顺序：

```text
apps/backend/app/db/models/user_memory.py
apps/backend/app/schemas/memory.py
apps/backend/app/services/memory_service.py
apps/backend/app/agents/memory_agent.py
apps/backend/app/api/routes/memories.py
apps/backend/app/api/router.py
```

这一阶段的作用：

让项目从普通 RAG 变成 Agentic RAG。但记忆必须和企业知识库证据隔离。

记忆只用于：

- 用户偏好。
- 用户长期资料。
- 当前会话上下文。
- 回答风格。

记忆不能用于：

- 替代企业知识库事实。
- 编造制度依据。
- 绕过权限。

每个文件的意义：

- `user_memory.py`：保存长期记忆。
- `schemas/memory.py`：定义记忆的创建、更新、读取结构。
- `memory_service.py`：短期记忆、长期记忆、自动记忆抽取、合并、替换。
- `memory_agent.py`：在 Agent 前后加载和更新记忆。
- `routes/memories.py`：让用户管理自己的记忆。

记忆状态：

```text
active
pending
superseded
ignored
```

验证：

先说：

```text
I prefer concise answers
```

再说：

```text
I prefer detailed answers
```

检查旧偏好是否变为 `superseded`，新偏好是否为 `active`。

## 13. 加反馈、审计、日志和管理指标

建议顺序：

```text
apps/backend/app/db/models/feedback.py
apps/backend/app/db/models/audit_log.py
apps/backend/app/db/models/llm_call_log.py
apps/backend/app/db/models/retrieval_log.py
apps/backend/app/schemas/feedback.py
apps/backend/app/schemas/admin.py
apps/backend/app/schemas/llm_log.py
apps/backend/app/schemas/retrieval_log.py
apps/backend/app/services/feedback_service.py
apps/backend/app/services/admin_service.py
apps/backend/app/services/audit_service.py
apps/backend/app/api/routes/feedbacks.py
apps/backend/app/api/routes/admin.py
apps/backend/app/api/routes/llm_logs.py
apps/backend/app/api/routes/retrieval_logs.py
apps/backend/app/api/router.py
```

这一阶段的作用：

企业级项目不能只看“回答出来了”。你还要知道：

- 用了哪些 chunk。
- 调了几次模型。
- token 和延迟是多少。
- 哪些操作被拒绝。
- 用户是否满意。
- 管理员能否看到近期问题。

每个模块的意义：

- `retrieval_logs`：排查召回、排序、引用问题。
- `llm_call_logs`：排查模型调用、token、成本、延迟和失败。
- `audit_logs`：记录安全相关动作。
- `feedbacks`：点赞/点踩。
- `admin_service.py`：聚合管理台指标。

验证：

完成一次问答后检查：

```text
GET /api/retrieval-logs
GET /api/llm-logs
POST /api/feedbacks
GET /api/admin/metrics
GET /api/admin/audit-logs
```

阶段完成标准：

你能从日志里复盘一次回答：用户问了什么、检索了什么、模型用了多少 token、结果有没有反馈。

## 14. 再写前端 API 封装和登录状态

建议顺序：

```text
apps/frontend/package.json
apps/frontend/index.html
apps/frontend/vite.config.ts
apps/frontend/tsconfig.json
apps/frontend/src/vite-env.d.ts
apps/frontend/src/stores/authStore.ts
apps/frontend/src/lib/auth.ts
apps/frontend/src/lib/api.ts
```

这一阶段的作用：

前端页面不要一开始就写。先把 API 类型、请求封装、token 保存、自动刷新和 SSE 解析写好，页面才不会到处散落 fetch 逻辑。

每个文件的意义：

- `authStore.ts`：本地保存 access token 和 refresh token。
- `auth.ts`：前端密码强度等轻量鉴权辅助。
- `api.ts`：后端类型、请求函数、错误处理、token refresh、SSE 解析。

学习重点：

- 前端不要自己判断最终权限，权限由后端决定。
- 前端 API 层要统一处理 401 refresh。
- 上传文件使用 `FormData`，不要强行设置 JSON。
- SSE 要处理 frame、event、data 和错误事件。

验证：

```bash
cd apps/frontend
npm run build
```

阶段完成标准：

前端类型检查和构建通过，API 封装可以被页面复用。

## 15. 最后写前端页面

建议顺序：

```text
apps/frontend/src/main.tsx
apps/frontend/src/App.tsx
apps/frontend/src/styles/globals.css
apps/frontend/src/pages/LoginPage.tsx
apps/frontend/src/pages/RegisterPage.tsx
apps/frontend/src/pages/KnowledgeBaseListPage.tsx
apps/frontend/src/pages/KnowledgeBaseDetailPage.tsx
apps/frontend/src/pages/ChatPage.tsx
apps/frontend/src/pages/MemoriesPage.tsx
apps/frontend/src/pages/AdminMetricsPage.tsx
```

这一阶段的作用：

把已经跑通的后端能力串成可操作产品。

按用户真实路径写页面：

```text
注册/登录
-> 知识库列表
-> 知识库详情和文档上传
-> 聊天问答
-> 记忆管理
-> 管理员指标
```

每个页面的意义：

- `App.tsx`：路由、登录态、布局导航。
- `LoginPage.tsx`：登录并保存 token。
- `RegisterPage.tsx`：注册第一个管理员或普通用户。
- `KnowledgeBaseListPage.tsx`：创建、查看、管理知识库。
- `KnowledgeBaseDetailPage.tsx`：上传文档、查看状态、查看 chunk。
- `ChatPage.tsx`：会话、SSE token、引用、检索日志、Agent trace、反馈。
- `MemoriesPage.tsx`：查看和管理长期记忆。
- `AdminMetricsPage.tsx`：查看指标、用户密级、审计日志。

验证：

从浏览器完整走一遍：

```text
注册
-> 创建知识库
-> 上传 demo/company_policy_demo.md
-> 等待 indexed
-> 提问
-> 查看 citations 和 Agent trace
-> 点赞/点踩
-> 查看 admin 指标
```

阶段完成标准：

一个新用户能不看 API 文档，只靠页面完成演示闭环。

## 16. 测试不要最后才补

建议顺序：

```text
apps/backend/tests/helpers.py
apps/backend/tests/test_config.py
apps/backend/tests/test_health.py
apps/backend/tests/test_auth_security.py
apps/backend/tests/test_knowledge_base_permissions.py
apps/backend/tests/test_document_service.py
apps/backend/tests/test_splitters.py
apps/backend/tests/test_advanced_retrieval.py
apps/backend/tests/test_answering_memory_context.py
apps/backend/tests/test_llm_provider_parsing.py
apps/backend/tests/test_rag_agent_memory_answer.py
apps/backend/tests/test_memory_service.py
apps/backend/tests/test_conversation_streaming.py
apps/backend/tests/test_agent_graph.py
apps/backend/tests/test_feedback_admin_metrics.py
apps/backend/tests/test_rag_eval_metrics.py
```

这一阶段的作用：

测试不是为了凑覆盖率，而是为了保护每个边界：

- 配置是否安全。
- 鉴权是否可靠。
- 用户能不能越权。
- 文档能不能重复上传。
- 删除失败会不会误删数据库记录。
- 检索排序是否可解释。
- Agent trace 是否完整。
- 记忆状态是否正确流转。

学习重点：

- 每个 service 都尽量有单元测试。
- 权限和删除逻辑优先写测试。
- RAG 评估指标可以轻量，但一定要可重复。

验证：

```bash
$env:PYTHONPATH="apps/backend"
python -m unittest discover -s apps/backend/tests
```

阶段完成标准：

后端测试全部通过。

## 17. 最后写脚本、验收和讲解文档

建议顺序：

```text
scripts/verify_migrations.py
scripts/smoke_demo.py
scripts/evaluate_rag.py
scripts/run_eval.py
scripts/check_project.py
docs/api.md
docs/rag_pipeline.md
docs/agent_orchestration.md
docs/evaluation.md
docs/manual_acceptance_checklist.md
docs/technical_pipeline_guide.md
docs/interview_prep_guide.md
docs/resume_project.md
```

这一阶段的作用：

代码写完不是项目完成。企业级项目还需要别人能启动、验收、复现、讲清楚。

每个脚本的意义：

- `verify_migrations.py`：确认 Alembic 迁移和模型一致。
- `smoke_demo.py`：自动跑注册、建库、上传、入库、问答。
- `evaluate_rag.py`：计算 RAG 指标。
- `run_eval.py`：对服务接口跑评估集。
- `check_project.py`：一键质量门禁。

每类文档的意义：

- `api.md`：接口说明。
- `rag_pipeline.md`：RAG 流程说明。
- `agent_orchestration.md`：Agent 编排说明。
- `evaluation.md`：评估方法说明。
- `manual_acceptance_checklist.md`：人工验收路径。
- `technical_pipeline_guide.md`：完整技术链路讲解。
- `interview_prep_guide.md`：面试讲解用。
- `resume_project.md`：简历描述用。

最终验证：

```bash
python scripts/check_project.py
```

如果服务已经启动，再跑：

```bash
python scripts/check_project.py --with-smoke
```

阶段完成标准：

别人能按 README 启动项目，按验收清单跑通主流程，按文档理解架构。

## 最小 MVP 可以停在哪里

如果你只是想先手敲一个能展示的版本，可以先做到第 10 步：

```text
启动环境
-> FastAPI 健康检查
-> 注册登录
-> 创建知识库
-> 上传文档
-> Worker 解析切分
-> Embedding + Qdrant
-> 非流式 RAG 问答
-> SSE 流式会话
```

这时已经可以证明项目核心价值：

```text
企业文档可以被安全入库、检索、引用并回答
```

第 11 步之后属于增强能力：

```text
Agent 编排
-> 长短期记忆
-> 反馈和管理指标
-> 完整前端体验
-> 评估和文档
```

## 每一步手敲时的思考问题

敲代码时，不要只是复制。每个阶段都问自己：

1. 这个文件属于哪一层？
2. 它能不能被别的模块复用？
3. 它有没有越界做别的层的事情？
4. 这个阶段最小可验证结果是什么？
5. 如果出错，我应该看 API、service、数据库、worker、向量库还是前端？

一些具体判断：

- `routes/*` 只处理 HTTP 请求、依赖注入和响应模型。
- `services/*` 承载业务流程和权限判断。
- `db/models/*` 只描述持久化结构。
- `schemas/*` 只描述请求响应数据。
- `rag/*` 只做解析、切分、检索、回答上下文等 RAG 能力。
- `llm/provider.py` 只封装模型调用。
- `agents/*` 只做编排，不重新实现权限和检索。
- `workers/*` 只做异步耗时任务。
- `frontend/src/lib/api.ts` 统一和后端通信。
- `frontend/src/pages/*` 只组织用户界面和交互。

## 推荐学习路线

第一遍：只敲到 MVP。

```text
第 1 步到第 10 步
```

目标是理解完整主链路，不追求所有增强能力。

第二遍：补 Agent 和记忆。

```text
第 11 步到第 12 步
```

目标是理解“普通 RAG”和“Agentic RAG”的区别。

第三遍：补工程化。

```text
第 13 步到第 17 步
```

目标是理解企业项目为什么需要日志、审计、测试、脚本和验收文档。

如果你能按这条顺序手敲一遍，并且每一步都能解释“为什么现在写这个文件”，你对这个项目的理解就不会停在表面代码，而会进入真实工程结构。
