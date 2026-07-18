# API 参考

本文档记录当前后端已经实现的 HTTP API。权威交互文档仍以运行时 OpenAPI 为准：

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

默认 API 前缀来自 `API_PREFIX`，本项目示例均使用 `/api`。

## 认证

除注册、登录、刷新、登出、健康检查外，业务接口默认需要：

```http
Authorization: Bearer <access_token>
```

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册用户并返回 access token、refresh token 和用户信息 |
| POST | `/api/auth/login` | 邮箱密码登录 |
| POST | `/api/auth/refresh` | 使用 refresh token 轮换新 token |
| POST | `/api/auth/logout` | 撤销 refresh token |
| GET | `/api/me` | 获取当前登录用户，包含 `security_level` 安全级别和部门信息 |

用户使用 `security_level` 表示安全级别，范围为 `1..5`。公开知识库和部门知识库的检索、文档查看和 chunk 查看只返回 `文档密级 <= 用户安全级别` 的内容；私人知识库只按成员权限隔离，创建后默认仅 owner 可见。
任何环境的空数据库中，第一个注册用户都会自动成为 `L5` 管理员；后续注册用户默认为普通 `L1`。生产环境不应将未初始化实例暴露给不可信网络。

## 部门

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/departments` | 当前登录用户查看部门列表 |
| POST | `/api/departments` | 管理员创建部门 |

## 运行状态

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/health` | 进程存活检查，只表示 FastAPI 可响应 |
| GET | `/api/ready` | 依赖就绪检查，覆盖 database、redis、pgvector、minio 和 Celery worker |

`/api/ready` 在任一依赖异常时返回 `503`，响应体会包含每个依赖的状态和错误摘要。

## 知识库

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/knowledge-bases` | 当前用户可访问的知识库列表 |
| POST | `/api/knowledge-bases` | 创建知识库 |
| GET | `/api/knowledge-bases/{kb_id}` | 查看知识库详情 |
| PATCH | `/api/knowledge-bases/{kb_id}` | 更新知识库名称、描述或可见性 |
| DELETE | `/api/knowledge-bases/{kb_id}` | 删除知识库及其文档、向量 |

权限边界由服务层按当前用户校验。私人知识库创建后默认只有 owner 成员，只允许 owner/editor/viewer 成员访问；公开知识库对所有登录用户可读可问。
部门知识库对同部门用户可读可问，文档维护需要 owner/editor 或管理员。普通用户可以创建私有知识库，也可以在已归属部门后创建本部门知识库；只有管理员可以创建、发布和管理公开知识库。

## 文档

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/knowledge-bases/{kb_id}/documents` | 上传文档，使用 `multipart/form-data` 字段 `file` 和可选 `security_level` |
| GET | `/api/knowledge-bases/{kb_id}/documents` | 查看知识库文档列表 |
| GET | `/api/documents/{document_id}` | 查看文档状态、chunk 数和错误信息 |
| DELETE | `/api/documents/{document_id}` | 删除文档和对应向量 |
| GET | `/api/documents/{document_id}/chunks` | 查看文档切分后的 chunk |

当前支持的主要演示格式包括 PDF、DOCX、TXT、MD、CSV。上传后由 worker 执行解析、切分、Embedding，并将 embedding 与 chunk 一起写入 PostgreSQL。同一知识库内重复上传相同文件内容会返回 `409`，避免重复入库和重复向量。

文档密级会写入 `documents.security_level` 和 `document_chunks.security_level`。Dense 查询在 PostgreSQL 中按知识库、owner、文档状态、模型/维度和 `security_level <= current_user.security_level` 共同过滤后，再以 pgvector 排序。长期记忆 embedding 存在 `user_memories` 行中，并按 active 状态、用户、模型和维度过滤。
私人知识库的 owner/editor 可以上传和删除文档，上传时后端统一使用默认密级；部门知识库需要 owner/editor 或管理员维护；公开知识库的文档只能由管理员维护。越权调用会返回 `403`，并写入审计日志。

## 问答与会话

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/knowledge-bases/{kb_id}/ask` | 对单个知识库发起非流式问答，返回答案、引用和检索日志 |
| GET | `/api/conversations` | 当前用户会话列表，可按 `knowledge_base_id` 过滤 |
| POST | `/api/conversations` | 创建会话 |
| GET | `/api/conversations/{conversation_id}` | 查看会话详情 |
| DELETE | `/api/conversations/{conversation_id}` | 删除会话及消息 |
| GET | `/api/conversations/{conversation_id}/messages` | 查看会话消息 |
| POST | `/api/conversations/{conversation_id}/messages/stream` | SSE 流式问答 |

非流式问答请求体：

```json
{
  "question": "住宿报销上限是多少？",
  "top_k": 5,
  "search_scope": "single"
}
```

`search_scope` 支持 `single`、`department`、`public`、`accessible` 和兼容别名 `all`。默认 `single` 只检索当前知识库；`department` 只检索指定或当前部门的部门知识库；`public` 只检索公开知识库；`accessible`/`all` 检索当前用户全部可访问知识库。检索日志会记录 `scope_type` 和 `searched_knowledge_base_ids`。

SSE 流式响应使用 `text/event-stream`，事件包含 `conversation`、`user_message`、`trace`、`token`、`retrieval_log`、`agent_run`、`citations`、`assistant_message`、`done` 和 `error`。
如果检索不到当前用户可访问的依据，回答会说明依据不足，并给出改写问题、检查入库状态或联系管理员调整密级的建议。

## Agent

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/agent-runs` | 查看当前用户 Agent run，可按知识库、会话或消息过滤 |
| POST | `/api/agent-runs` | 运行 Agent 编排流程 |
| GET | `/api/agent-runs/{run_id}` | 查看单次 Agent run 和 trace |

Agent trace 用于展示模型步骤、`memory(query)` / `rag(query)` 工具调用、最终回答与记忆更新链路。

## 记忆

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/memories` | 当前用户长期记忆列表，可按 `status` 过滤 |
| GET | `/api/memories/export` | 导出当前用户的记忆、记忆事件、召回日志和异步更新任务 |
| GET | `/api/memories/recall-metrics` | 当前用户记忆召回质量聚合指标 |
| POST | `/api/memories/reconcile` | 检查记忆重复及缺失/无效 embedding，支持 dry-run/apply |
| GET | `/api/memories/update-jobs` | 当前用户异步记忆更新任务列表，可按 `status` 过滤 |
| POST | `/api/memories/update-jobs/{job_id}/retry` | 重试 queued 或 failed 记忆更新任务 |
| POST | `/api/memories` | 手动创建长期记忆 |
| PATCH | `/api/memories/{memory_id}` | 更新记忆内容、状态、分类或类型 |
| POST | `/api/memories/{memory_id}/approve` | 批准一条 pending 记忆并激活 |
| POST | `/api/memories/{memory_id}/reject` | 拒绝一条 pending 记忆并标记为 ignored |
| POST | `/api/memories/{memory_id}/restore` | 恢复一条 soft-deleted 记忆 |
| DELETE | `/api/memories/{memory_id}` | soft delete 长期记忆 |
| DELETE | `/api/memories/{memory_id}/purge` | 永久清除单条记忆和对应向量索引，保留 purge 审计快照 |

自动长期记忆采用两阶段流程：Candidate Extractor 只从当前 turn 提取候选，系统检索相关旧记忆后再由 Memory Judge 判断 `independent/equivalent/refinement/replacement/uncertain/discard`，服务层映射为 `create/ignore/update/supersede/pending/ignore`。向量相似度只做无阈值 top-K 候选排序，不触发自动语义合并。最终还会执行 evidence、敏感信息、目标归属、exact hash、revision、唯一约束和事务校验；模型建议不等于直接写库。

## 反馈、日志与指标

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/feedbacks` | 当前用户反馈列表，可按 `message_id` 过滤 |
| POST | `/api/feedbacks` | 对回答消息提交点赞或点踩 |
| GET | `/api/llm-logs` | LLM 调用日志，可按 `conversation_id`、`agent_name` 过滤 |
| GET | `/api/llm-logs/{log_id}` | 查看单条 LLM 调用日志 |
| GET | `/api/retrieval-logs` | 检索日志，可按知识库、会话或消息过滤 |
| GET | `/api/retrieval-logs/{log_id}` | 查看单条检索日志 |
| GET | `/api/admin/metrics` | 聚合指标，包括请求、延迟、Token、反馈和最近错误 |
| GET | `/api/admin/users` | 管理员查看用户安全级别、部门和状态 |
| PATCH | `/api/admin/users/{user_id}` | 管理员更新用户 `security_level`、`department_id`、`is_active` 或 `is_admin` |
| GET | `/api/admin/audit-logs` | 查看关键操作审计；管理员看全局，普通用户看自己的记录 |
| GET | `/api/admin/external-cleanup-jobs` | 管理员查看 MinIO 外部清理任务 |
| POST | `/api/admin/external-cleanup-jobs/{job_id}/retry` | 重试可恢复的外部清理任务 |
| POST | `/api/admin/retention/run` | 预览或执行运营数据保留策略 |

日志接口只返回当前用户可见数据；`/api/admin/users` 与用户更新接口需要管理员权限。前端 `/admin` 也只对管理员开放。
