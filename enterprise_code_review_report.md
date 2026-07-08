# Agentic RAG 企业级上线设计审查报告

日期：2026-07-07  
审查目标：找出当前代码中不适合真实生产上线的设计，并给出可落地整改方向。  
审查范围：后端认证与权限、RAG/Agent、文档处理、异步任务、数据库迁移、前端认证状态、部署与可观测性。  
产出方式：只修改本报告文件，未修改业务源码。

## 约束执行说明

- 已读取本文件 `enterprise_code_review_report.md`。
- 审查源码时以 `.py`、`.ts`、`.tsx`、`.json`、`.toml`、`Dockerfile`、`docker-compose.yml` 等非 Markdown 文件为主。
- 过程中有一次 `rg` 命令因为 PowerShell 引号处理不严，意外输出了少量非目标 Markdown 的匹配行；这些内容已弃用，未作为本报告判断依据。
- 之后所有检索均限定为源码/配置路径或显式排除 Markdown。

## 总体结论

这个项目已经具备企业 RAG 系统雏形：FastAPI 分层、认证、权限角色、知识库可见性、密级、RAG 日志、Agent 编排、Celery 文档任务、Qdrant、MinIO、Redis、PostgreSQL 迁移、前端管理页面和测试基础都已经存在。

但如果按“真正上线使用的系统”标准看，当前主要风险不在功能缺失，而在以下几类设计边界：

- 认证与 token 生命周期缺少并发安全和运营安全。
- 权限语义分散在服务层，数据库和历史日志缺少强约束与复核。
- 数据库、对象存储、向量库之间没有事务一致性策略。
- 文档上传/解析/索引缺少资源隔离、幂等和任务租约。
- RAG 答案仍过度依赖 prompt 约束，缺少确定性拒答和引用一致性保证。
- 部署配置仍偏开发环境，镜像、端口、TLS、密钥、启动失败策略需要生产硬化。
- 前端 refresh/logout 状态不完全一致，token 存储方式不适合高安全等级系统。

下面按优先级列出问题。

## P0：上线前必须处理

### 1. 首个注册用户自动成为管理员，存在公开注册与并发竞态

位置：

- `apps/backend/app/services/auth_service.py:29`
- `apps/backend/app/services/auth_service.py:36`
- `apps/backend/app/api/routes/auth.py:30`

问题：

`register_user()` 用 `count(User.id) == 0` 判断首个用户，并直接授予 `is_admin=True`、`security_level=5`。如果注册接口开放到公网，攻击者可以抢首个账号；如果两个注册请求并发进入，也可能同时看到用户数为 0。

上线影响：

- 管理员权限来源不可审计、不可控。
- 高权限账号可能被并发或部署窗口意外创建。
- 一旦服务在空库环境暴露，属于高风险认证设计。

整改方向：

- 取消公开注册中的自动管理员逻辑。
- 改成显式 bootstrap 流程：部署期 CLI、一次性 bootstrap token、管理员邀请制或运维后台创建。
- 如果必须保留首个管理员机制，至少使用数据库事务锁、唯一约束或 advisory lock 保证只有一个 bootstrap admin。

### 2. Refresh token 轮换不是原子消费，存在重放窗口

位置：

- `apps/backend/app/services/auth_service.py:87`
- `apps/backend/app/services/auth_service.py:90`
- `apps/backend/app/services/auth_service.py:103`
- `apps/backend/app/db/models/refresh_token.py:15`

问题：

`refresh_tokens()` 先查 token，再判断 `revoked_at`，最后撤销旧 token 并签发新 token。两个并发 refresh 请求可能同时通过检查，最终得到两个有效 refresh token。

上线影响：

- refresh token 被窃取后，服务端难以及时识别 replay。
- 用户“刷新一次失效旧 token”的安全语义不成立。
- 强制登出、风险会话吊销、事故响应都会变弱。

整改方向：

- 使用原子更新消费旧 token，例如 `UPDATE ... WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > now()`，并检查受影响行数。
- 引入 token family、rotation counter 和 reuse detection。
- 一旦发现已撤销 token 被再次使用，吊销同一 token family 下所有 refresh token。

### 3. 前端 refresh 后状态不同步，登出可能撤销旧 token

位置：

- `apps/frontend/src/lib/api.ts:786`
- `apps/frontend/src/lib/api.ts:790`
- `apps/frontend/src/App.tsx:72`
- `apps/frontend/src/stores/authStore.ts:9`

问题：

`refreshStoredAuth()` 会把新的 refresh token 写入 `localStorage`，但 React `App` state 未同步。`handleLogout()` 使用的是旧 `state.auth.refreshToken`，可能只撤销旧 token，而最新 refresh token 仍然有效。

上线影响：

- 用户以为已经退出，但新 refresh token 可能还可继续换 access token。
- 强制登出语义不完整。
- 和后端 refresh token 非原子问题叠加后，风险更高。

整改方向：

- 建立单一 auth store，refresh 成功时同时更新内存状态与持久状态。
- logout 时读取最新 `getStoredAuth()`，不要依赖可能过期的 React state。
- 高安全系统建议改为 httpOnly、SameSite、Secure cookie 或 BFF session 模式，避免 refresh token 暴露给 JS。

### 4. 多知识库检索日志和 Agent run 的历史权限复核不完整

位置：

- `apps/backend/app/services/retrieval_log_service.py:25`
- `apps/backend/app/services/retrieval_log_service.py:53`
- `apps/backend/app/services/retrieval_log_service.py:72`
- `apps/backend/app/services/agent_service.py:75`
- `apps/backend/app/services/agent_service.py:94`

问题：

跨范围检索会把真实搜索过的知识库写到 `searched_knowledge_base_ids`，但读取 retrieval log / agent run 时主要复核 `knowledge_base_id`。当 `knowledge_base_id=None` 或只代表 primary KB 时，没有逐项复核 `searched_knowledge_base_ids`。

上线影响：

- 权限被撤销后，用户仍可能看到历史候选片段、selected chunk 预览、引用和回答。
- RAG 日志保存了 `content_preview`，这不是纯元数据，具有数据泄露风险。
- 不符合企业数据隔离、权限变更即时生效和审计最小披露要求。

整改方向：

- 读取日志时对 `searched_knowledge_base_ids` 逐项做当前权限复核。
- 对日志中的候选片段、预览内容、引用、回答建立数据保留策略。
- 企业场景建议日志只保存 chunk id、score、route 等可追溯元数据；内容预览按权限动态二次读取或脱敏。

### 5. `security_level` 在 private KB 中被显式绕过

位置：

- `apps/backend/app/services/knowledge_base_service.py:323`
- `apps/backend/app/services/document_service.py:172`
- `apps/backend/app/services/document_service.py:190`
- `apps/backend/app/services/document_service.py:327`

问题：

single scope 搜索 private KB 时，如果用户不是管理员但 KB 是 private，也会给 `MAX_SECURITY_LEVEL`。文档列表/读取同样只在非 private KB 下按用户 `security_level` 过滤。

上线影响：

- 如果 `security_level` 表示企业密级，private KB 成员可以读取超过自身密级的文档。
- 对象权限和密级权限语义冲突，容易产生误配置。
- 安全模型对管理员和业务方都不够清晰。

整改方向：

- 明确 `security_level` 是“企业密级”还是“共享知识库密级”。
- 如果是企业密级，所有 visibility 都必须统一按用户密级过滤，包括 private。
- 如果 private KB 确实有例外，需要在权限矩阵、测试和 UI 文案中显式说明。

### 6. 删除流程先删外部资源，再提交数据库删除，容易出现不可恢复不一致

位置：

- `apps/backend/app/services/document_service.py:225`
- `apps/backend/app/services/document_service.py:250`
- `apps/backend/app/services/document_service.py:251`
- `apps/backend/app/services/knowledge_base_service.py:160`
- `apps/backend/app/services/knowledge_base_service.py:161`

问题：

删除文档/知识库时，当前流程先删除 Qdrant 向量和 MinIO 对象，然后才 `db.delete()` 和 `db.commit()`。如果外部资源删除成功，但数据库提交失败，会留下 DB 记录仍在、对象/向量已丢的状态。

上线影响：

- 数据库、对象存储、向量库不一致。
- 后续下载、检索、审计和重建任务异常。
- 没有可靠补偿机制，人工修复成本高。

整改方向：

- 使用软删除状态：先在 DB 标记 `deleting` 或 `deleted_pending_cleanup`。
- DB commit 成功后，通过 outbox/saga 异步清理 MinIO/Qdrant。
- 清理任务要幂等、可重试、可告警，并提供一致性修复脚本。

## P1：上线前高优先级整改

### 7. 登录缺少限流、失败审计和 dummy hash

位置：

- `apps/backend/app/services/auth_service.py:50`
- `apps/backend/app/services/auth_service.py:52`
- `apps/backend/app/api/routes/auth.py:35`

问题：

用户不存在时直接返回，用户存在时才执行 bcrypt 校验，存在时序差异。登录、刷新、登出接口没有限流，也没有失败审计。

整改方向：

- 对不存在用户执行固定 dummy hash，降低用户枚举侧信道。
- 对 login/refresh 增加 IP + email/user 维度限流。
- 记录失败登录、异常 refresh、token replay、登出失败等安全事件。

### 8. 生产环境判断未 `strip()`，可能绕过生产配置校验

位置：

- `apps/backend/app/core/config.py:69`
- `apps/backend/app/core/config.py:75`
- `apps/backend/app/core/config.py:85`

问题：

生产判断使用 `settings.app_env.lower()`。如果环境变量误写成 `production `，会绕过生产校验，包括 SQLite、auto create tables、弱 JWT secret、缺少 LLM/Embedding key 等检查。

整改方向：

- 统一使用 `settings.app_env.strip().lower()`。
- 对未知环境名 fail closed，不要默默当 development。
- 在启动日志中输出规范化后的环境名。

### 9. Qdrant 初始化失败只 warning，生产可能“启动成功但核心能力不可用”

位置：

- `apps/backend/app/main.py:19`
- `apps/backend/app/main.py:20`
- `apps/backend/app/rag/vector_store.py:37`
- `apps/backend/app/rag/vector_store.py:53`

问题：

应用启动时 `ensure_qdrant_collection()` 失败只打 warning。payload index 创建失败也被 `continue` 静默跳过。

上线影响：

- 服务进程健康，但 RAG 检索不可用或性能退化。
- 索引缺失不易被监控发现。

整改方向：

- 生产环境启动时 Qdrant collection / payload index 初始化失败应 fail fast。
- readiness 检查应包含 collection 与关键 payload index 状态。
- 对索引创建失败记录结构化日志和告警。

### 10. 文档上传全量读入内存后才校验大小

位置：

- `apps/backend/app/api/routes/documents.py:33`
- `apps/backend/app/services/document_service.py:63`

问题：

上传接口先 `await file.read()` 全量读入内存，然后才用 `len(file_bytes)` 判断大小。大文件或并发上传会先消耗后端内存。

整改方向：

- 在反向代理和 ASGI 层设置 request body limit。
- 服务端改为流式读取，并边读边累计大小限制。
- 对上传并发增加用户级和全局限流。

### 11. 文件准入只看扩展名，缺少内容识别、扫描和解析隔离

位置：

- `apps/backend/app/services/document_service.py:23`
- `apps/backend/app/services/document_service.py:63`
- `apps/backend/app/rag/loaders.py:54`
- `apps/backend/app/rag/loaders.py:71`

问题：

上传文件只按文件名后缀判断，`mime_type` 来自客户端。PDF/DOCX/CSV 解析没有 magic sniffing、AV 扫描、解析超时、沙箱或资源限制。

整改方向：

- 使用 magic number / MIME sniffing 校验真实类型。
- 接入恶意文件扫描。
- 文档解析放在受限 worker 中，设置 CPU、内存、页数、行数、解压比和超时限制。
- 对解析失败原因做分类，避免把 parser 细节直接暴露给用户。

### 12. 文档去重和反馈去重只在应用层，缺少数据库唯一约束

位置：

- `apps/backend/app/services/document_service.py:76`
- `apps/backend/app/services/document_service.py:78`
- `apps/backend/app/db/models/document.py:24`
- `apps/backend/app/services/feedback_service.py:18`
- `apps/backend/app/db/models/feedback.py:17`

问题：

文档上传通过应用层查询 `content_hash` 去重，但没有 `(knowledge_base_id, content_hash)` 唯一约束。反馈同理，应用层检查已有反馈，但没有 `(user_id, message_id)` 唯一约束。

整改方向：

- 增加数据库唯一约束或部分唯一约束。
- 捕获 `IntegrityError` 并返回 409。
- 把“关键业务不变量”下沉到数据库，不只依赖服务层。

### 13. 文档索引任务缺少 document 级锁、任务租约和版本 guard

位置：

- `apps/backend/app/workers/document_tasks.py:20`
- `apps/backend/app/workers/document_tasks.py:31`
- `apps/backend/app/workers/document_tasks.py:50`
- `apps/backend/app/workers/document_tasks.py:53`
- `apps/backend/app/workers/document_tasks.py:73`

问题：

重复 Celery 任务可能同时处理同一个文档，并交错执行删除 chunk、删除向量、插入 chunk、upsert 向量、更新状态。

上线影响：

- 重复 chunk、过期向量、状态覆盖。
- 一个旧任务可能覆盖新任务结果。

整改方向：

- 引入 `index_job_id`、`processing_lease_until`、`indexed_version`。
- 状态更新使用条件更新：只有持有当前 lease/job 的任务能写入。
- Qdrant point id 使用稳定幂等规则，并携带 document version。

### 14. 检索无结果仍调用 LLM，拒答完全依赖 prompt

位置：

- `apps/backend/app/services/qa_service.py:88`
- `apps/backend/app/services/qa_service.py:90`
- `apps/backend/app/rag/answering.py:49`
- `apps/backend/app/rag/answering.py:56`
- `apps/backend/app/llm/provider.py:424`

问题：

当 `retrieval.selected_chunks=[]` 时，服务仍调用 LLM，只靠 prompt 要求模型拒答。

上线影响：

- 增加幻觉风险。
- 浪费成本和延迟。
- 企业知识问答的“无证据不回答”应是确定性逻辑，而不只是 prompt。

整改方向：

- 在服务层判定无证据时直接返回固定拒答。
- 仍记录 retrieval log 和 audit event，但不调用 LLM。
- 对低召回结果设置最小证据阈值，而不是只看 top_k。

### 15. Summary/Writing 二次生成会造成引用漂移

位置：

- `apps/backend/app/agents/summary_agent.py:15`
- `apps/backend/app/agents/summary_agent.py:19`
- `apps/backend/app/agents/writing_agent.py:16`
- `apps/backend/app/agents/writing_agent.py:20`
- `apps/backend/app/services/agent_service.py:54`

问题：

summary/writing 先调用 RAG 生成有引用的答案，再让第二次 LLM 总结或起草，但最终仍保存第一次 RAG 的 citations。用户可能误以为二次生成文本每一句都由这些引用支撑。

整改方向：

- 二次生成后重新做引用对齐。
- 或把输出明确标记为“基于已检索答案的改写”，不要展示为逐句证据引用。
- 对企业问答优先返回结构化 grounded answer，减少自由二次改写。

### 16. LLM 调用失败不会落库，观测数据偏乐观

位置：

- `apps/backend/app/llm/provider.py:494`
- `apps/backend/app/llm/provider.py:529`
- `apps/backend/app/services/llm_log_service.py:12`

问题：

provider 异常直接抛 `RuntimeError`，`create_llm_call_log()` 只记录成功返回的 `LlmCompletion`。失败调用的 provider、model、latency、错误类型不会进入 LLM 日志。

整改方向：

- 增加失败日志写入路径或 provider wrapper。
- 记录 provider、model、agent_name、conversation_id、latency、error type、是否 streaming。
- 指标中区分业务失败、模型失败、网络失败、超时和取消。

### 17. 审计写入吞掉所有异常

位置：

- `apps/backend/app/services/audit_service.py:11`
- `apps/backend/app/services/audit_service.py:37`

问题：

`record_audit_event()` 捕获所有异常后 rollback 并返回 `None`。关键安全事件可能无声丢失。

整改方向：

- 至少写 fallback logger 和 metrics。
- 管理员变更、权限拒绝、删除失败、token replay 等关键事件应触发告警。
- 高安全场景下，关键审计失败应 fail closed 或进入本地 outbox。

### 18. 流式会话每个请求创建线程，缺少超时、背压和并发控制

位置：

- `apps/backend/app/services/conversation_service.py:249`
- `apps/backend/app/services/conversation_service.py:283`
- `apps/backend/app/services/conversation_service.py:293`
- `apps/backend/app/services/conversation_service.py:308`
- `apps/backend/app/api/routes/conversations.py:77`

问题：

每个流式请求创建一个 daemon thread 和无界 Queue。取消依赖 generator finally 设置 `cancel_event`，但模型/网络阻塞时没有全局超时和强制回收。

上线影响：

- 慢 LLM 或断连客户端可能拖住线程资源。
- 高并发下容易资源耗尽。

整改方向：

- 使用 async streaming 或后台任务池，设置最大并发。
- Queue 设置 maxsize，避免内存无界增长。
- 为 LLM 调用、检索、整体会话设置 deadline。
- 记录 cancelled/timeout 状态，不要只返回空或普通 error。

### 19. 数据模型与迁移中的外键删除语义不一致

位置：

- `apps/backend/alembic/versions/20260703_0001_initial_schema.py:134`
- `apps/backend/alembic/versions/20260703_0001_initial_schema.py:172`
- `apps/backend/alembic/versions/20260703_0001_initial_schema.py:197`
- `apps/backend/alembic/versions/20260705_0006_conversation_search_targets.py:27`
- `apps/backend/app/db/models/conversation.py:19`
- `apps/backend/app/db/models/retrieval_log.py:19`
- `apps/backend/app/db/models/agent_run.py:19`

问题：

初始迁移中 `conversations`、`retrieval_logs`、`agent_runs` 对 `knowledge_bases` 的外键是 `CASCADE`，模型中后来变为 `SET NULL`，迁移 0006 只修改 nullable，没有明确重建外键 on delete 行为。

上线影响：

- 删除知识库时，历史会话、检索日志、Agent run 可能被级联删除。
- 与模型意图和审计保留需求不一致。

整改方向：

- 编写迁移显式 drop/recreate 外键，统一为期望的 `SET NULL` 或业务定义行为。
- 增加 migration/schema diff 校验。
- 建立历史日志保留策略，避免重要审计数据被业务删除级联清理。

### 20. 长期记忆可能把助手回答沉淀为用户记忆来源

位置：

- `apps/backend/app/services/memory_service.py:150`
- `apps/backend/app/services/memory_service.py:153`
- `apps/backend/app/services/memory_service.py:353`
- `apps/backend/app/services/memory_service.py:357`
- `apps/backend/app/services/memory_service.py:418`

问题：

记忆编辑器虽然 prompt 要求只保存用户原话支持的事实，但 `memory_source_text()` 把 User 与 Assistant 都拼入 source_text，代码没有强校验 evidence 必须来自用户消息。

上线影响：

- RAG 回答中的企业内容可能被误沉淀为用户长期记忆。
- 用户画像和知识库事实边界混淆。

整改方向：

- evidence 必须是用户消息的子串或结构化引用。
- 禁止从 assistant answer 创建/更新用户长期记忆。
- 高敏感或不确定记忆默认 pending，需用户确认。

## P2：生产化设计改造

### 21. BM25 使用 `ILIKE '%term%'` 拉候选，大规模知识库性能风险高

位置：

- `apps/backend/app/rag/advanced_retrieval.py:298`
- `apps/backend/app/rag/advanced_retrieval.py:310`
- `apps/backend/app/rag/advanced_retrieval.py:347`
- `apps/backend/app/rag/advanced_retrieval.py:562`

问题：

BM25 候选通过数据库 `ILIKE` 模糊匹配拉取，再在 Python 中打分。大知识库、多租户、高并发下会快速退化。

整改方向：

- 使用 PostgreSQL FTS、OpenSearch/Elasticsearch 或专用稀疏检索引擎。
- 对候选数、扫描行数、查询耗时设置硬上限。
- 增加召回耗时、候选数、命中率和降级指标。

### 22. 部署配置仍是开发态，不适合直接生产上线

位置：

- `infra/docker-compose.yml:32`
- `infra/docker-compose.yml:46`
- `infra/docker-compose.yml:73`
- `infra/docker-compose.yml:77`
- `apps/frontend/Dockerfile:12`

问题：

Compose 使用 Qdrant/MinIO `latest`，后端 `--reload` 和 bind mount，前端 Vite dev server，多项内部端口直接暴露到宿主机。

整改方向：

- 拆分 dev/prod compose 或 Helm/K8s 部署。
- 固定镜像 tag/digest。
- 前端使用静态构建 + Nginx/CDN。
- 后端禁用 reload，去除源码 bind mount，非 root 运行。
- 只暴露入口网关，Redis/Postgres/Qdrant/MinIO 放内部网络。

### 23. Qdrant/MinIO 缺少生产级认证、TLS 和密钥校验

位置：

- `apps/backend/app/core/config.py:22`
- `apps/backend/app/core/config.py:24`
- `apps/backend/app/core/config.py:25`
- `apps/backend/app/core/config.py:26`
- `infra/docker-compose.yml:46`

问题：

配置默认 MinIO 使用 `minioadmin/minioadmin`，`minio_secure=False`。Qdrant 配置只有 URL，没有 API key/TLS 相关配置。

整改方向：

- 生产环境校验 MinIO 不允许默认账号密码，必须启用 TLS 或内网 mTLS。
- 为 Qdrant 配置 API key/TLS，并在请求层带认证 header。
- 密钥进入 secret manager，不依赖普通 `.env` 作为生产唯一来源。

### 24. 数据库缺少枚举/范围 CheckConstraint

位置：

- `apps/backend/app/db/models/user.py:21`
- `apps/backend/app/db/models/knowledge_base.py:20`
- `apps/backend/app/db/models/knowledge_base.py:38`
- `apps/backend/app/db/models/conversation.py:49`
- `apps/backend/app/db/models/conversation.py:51`
- `apps/backend/app/db/models/user_memory.py:20`

问题：

`security_level`、`visibility`、member `role`、message `role/status`、memory `status` 等语义主要靠 Pydantic 和服务层。绕过服务层写库后，数据可能进入无效状态。

整改方向：

- 增加 `CheckConstraint` 或数据库 enum。
- 迁移前增加脏数据检测脚本。
- 把状态机合法迁移写成服务层函数和测试。

### 25. 前端错误处理和 SSE 解析不够健壮

位置：

- `apps/frontend/src/lib/api.ts:812`
- `apps/frontend/src/lib/api.ts:857`

问题：

FastAPI 422 的 `detail` 常为 list，当前只处理 string。SSE frame 的 JSON parse 没有降级保护，坏帧会直接中断整个 UI 流程。

整改方向：

- 后端统一错误响应模型。
- 前端格式化 Pydantic validation errors。
- SSE 单帧解析失败应局部报错，并保留可恢复状态。

### 26. Chat 单知识库选择与后端能力不一致

位置：

- `apps/frontend/src/pages/ChatPage.tsx:64`
- `apps/frontend/src/pages/ChatPage.tsx:65`
- `apps/backend/app/services/knowledge_base_service.py:321`

问题：

前端“单个知识库”只展示用户自己的 private KB，但后端 single scope 支持任意可访问 KB，包括 public/department。

整改方向：

- 单 KB 下拉展示所有可访问 KB。
- 用标签区分 private/department/public。
- 允许用户精确指定 public/department KB，而不是只能合并检索。

### 27. LLM 全局日志对所有用户可见，后续扩展存在信息暴露风险

位置：

- `apps/backend/app/services/llm_log_service.py:46`

问题：

`list_llm_call_logs()` 会返回当前用户日志以及 `user_id IS NULL` 的日志。当前日志字段不含完整 prompt，但以后如果系统任务或失败日志写入更多细节，可能造成横向可见。

整改方向：

- 全局日志默认只管理员可见。
- 普通用户只看自己的 conversation/agent 相关日志。
- 对错误信息做脱敏。

## 建议改造路线

### 第一阶段：先修安全语义和真实 bug

1. 移除公开注册自动管理员，改成 bootstrap/邀请制。
2. refresh token 改为原子轮换，增加 token family 和 replay detection。
3. 修复前端 refresh/logout 状态不一致。
4. 明确并修复 `security_level` 与 private KB 的权限语义。
5. 对 retrieval log / agent run 的 `searched_knowledge_base_ids` 做读取时权限复核。
6. 登录/refresh 增加限流、失败审计和 dummy hash。

### 第二阶段：修数据一致性和任务幂等

1. 删除文档/知识库改为软删除 + outbox/saga 清理外部资源。
2. 文档索引任务增加 lease/job_id/version guard。
3. 文档和反馈增加数据库唯一约束。
4. 修正迁移中外键 on delete 行为，避免历史日志被误删。
5. 关键审计失败增加 fallback、metrics 和告警。

### 第三阶段：修 RAG 可信度

1. 无检索证据时服务层直接确定性拒答。
2. summary/writing 输出重新对齐引用，或明确标记为改写。
3. 对 retrieval log 内容预览做权限复核、脱敏或延迟读取。
4. BM25 换为生产级全文检索或稀疏索引。
5. LLM 成功/失败/超时/取消都进入统一日志和指标。

### 第四阶段：生产部署硬化

1. 拆分 dev/prod 部署配置。
2. 固定镜像版本，不用 `latest`。
3. 后端禁用 reload，前端改静态构建。
4. 内部服务不直暴露宿主机。
5. MinIO/Qdrant 配置 TLS/API key，禁止默认密钥。
6. Qdrant collection/index 初始化在生产 fail fast，readiness 暴露索引状态。

### 第五阶段：质量与回归保障

1. 增加并发测试：首用户注册、refresh 轮换、重复上传、重复索引、重复反馈。
2. 增加权限回归：权限撤销后日志/引用/历史会话是否仍可见。
3. 增加迁移一致性检查：模型与真实数据库外键、索引、约束一致。
4. 增加上传安全测试：超大文件、伪造 MIME、恶意压缩、解析超时。
5. 增加前端契约测试：422、401 refresh、SSE 坏帧、logout token 同步。

## 本次结论

当前代码适合作为功能型原型或内部受控 Demo，但还不能按企业生产系统直接上线。优先级最高的不是加新功能，而是把认证、权限、数据一致性、任务幂等、RAG 拒答和部署安全这些边界收紧。

如果按上述路线改造，项目可以逐步从“可演示的 Agentic RAG”转为“可运营、可审计、可恢复、可控成本和可控风险的生产系统”。
