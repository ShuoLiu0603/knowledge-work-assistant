# Agentic RAG 企业级代码体检报告

日期：2026-07-06  
范围：后端认证/权限/RAG/Agent/文档处理/前端/API 契约/部署与测试  
约束：只读审查；未读取任何现有 Markdown 文件；未修改业务代码

## 总体结论

这个项目已经不是玩具级 Demo。它具备 FastAPI 分层、Alembic 迁移、Celery 异步任务、Qdrant 向量检索、MinIO 对象存储、权限角色、审计、RAG 日志、长期记忆和后端测试。

当前距离“企业级顶级项目”的主要差距集中在：

- 认证与 token 生命周期的并发安全
- 对象级权限和历史日志权限复核
- 数据库与外部资源的一致性
- 文档处理的幂等性和资源控制
- RAG 引用可信度与无证据拒答
- 生产部署硬化
- 前端 token 存储与状态一致性
- 可观测性、审计可靠性和契约测试

## 验证情况

- 已启动 3 个子智能体并行审查：认证安全、RAG/Agent、前端/部署/测试。
- 本地执行：`python scripts/check_project.py --skip-frontend --skip-compose`
- 后端测试结果：81 个测试通过。
- Python compile：通过。
- 迁移校验：通过。
- 额外检索/密级相关测试：11 个测试通过。
- 前端子智能体执行 TypeScript typecheck：通过。

## P0 / 高优先级问题

### 1. 首个注册用户自动成为管理员存在并发竞态

位置：

- `apps/backend/app/services/auth_service.py:29`
- `apps/backend/app/api/routes/auth.py:28`

问题：

`register_user()` 通过 `count(User.id) == 0` 判断首个用户，并授予 `is_admin=True` 和 `security_level=5`。公开注册接口允许外部请求进入。两个并发首注册请求可能同时看到用户数为 0，从而产生多个管理员。

影响：

- 管理员权限可被并发竞态意外扩大。
- 在公开部署环境中属于高风险认证设计。

建议：

- 取消公开注册路径中的自动管理员授予。
- 改为显式 bootstrap CLI、一次性 bootstrap token 或部署期初始化脚本。
- 如必须保留，使用数据库事务锁、唯一约束或 advisory lock 保证只有一个 bootstrap admin。

### 2. Refresh token 轮换不是原子操作

位置：

- `apps/backend/app/services/auth_service.py:87`
- `apps/backend/app/db/models/refresh_token.py:15`

问题：

`refresh_tokens()` 先查询 token，再判断 `revoked_at`，再撤销并签发新 token。两个并发 refresh 请求可能同时通过检查，最终获得两个有效的新 refresh token。

影响：

- token replay 检测薄弱。
- refresh token 被窃取后，服务端难以及时识别重放。

建议：

- 使用原子消费：`UPDATE refresh_tokens SET revoked_at = now() WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > now()`。
- 引入 token family、rotation counter、reuse detection。
- 发现旧 refresh token 重放时吊销同一 token family。

### 3. 静默 refresh 后前端登出可能撤销旧 token

位置：

- `apps/frontend/src/lib/api.ts:790`
- `apps/frontend/src/App.tsx:72`

问题：

`refreshStoredAuth()` 会保存新的 refresh token 到 localStorage，但 React `App` state 不同步。登出时仍使用旧 `state.auth.refreshToken`，后端只会撤销旧 token，当前有效 refresh token 可能未被撤销。

影响：

- 用户以为已退出，但最新 refresh token 仍可能有效。
- 事故响应和强制登出的语义不完整。

建议：

- 统一 auth store，refresh 成功时同步 React state。
- logout 时读取最新 `getStoredAuth()`。
- 更进一步：改用 httpOnly/SameSite/Secure cookie 或 BFF session。

### 4. 多知识库检索历史缺少完整权限复核

位置：

- `apps/backend/app/services/retrieval_log_service.py:53`
- `apps/backend/app/services/agent_service.py:75`
- `apps/backend/app/services/qa_service.py:79`

问题：

跨范围检索如 `department`、`public`、`accessible` 往往让 `knowledge_base_id=None`，真实检索范围保存在 `searched_knowledge_base_ids`。读取 retrieval log / agent run 时主要检查 `knowledge_base_id`，没有逐一复核 `searched_knowledge_base_ids`。

影响：

- 用户权限被撤销后，仍可能看到历史候选、片段预览、引用和回答。
- 对企业数据隔离和审计合规不友好。

建议：

- 对 `searched_knowledge_base_ids` 逐项执行读取时权限复核。
- 对历史 answer/citation 是否永久可见建立明确产品策略。
- 需要时对日志中的内容预览做脱敏或只保留 chunk id。

### 5. 删除流程先删外部资源，再提交数据库删除

位置：

- `apps/backend/app/services/document_service.py:204`
- `apps/backend/app/services/knowledge_base_service.py:157`

问题：

删除文档/知识库时，先删除 Qdrant 向量和 MinIO 对象，再 `db.delete()` 并提交数据库事务。若外部资源删除成功但数据库 commit 失败，会留下 DB 记录仍存在、对象/向量已丢失的不可恢复状态。

影响：

- 数据库与对象存储/向量库不一致。
- 后续检索、下载、审计和重建都可能异常。

建议：

- 使用软删除状态：先标记 `deleting`。
- 数据库事务提交后，通过 outbox/saga 异步清理外部资源。
- 清理任务可重试、幂等，并提供修复脚本。

### 6. `security_level` 对 private KB 被显式绕过

位置：

- `apps/backend/app/services/knowledge_base_service.py:318`
- `apps/backend/app/services/document_service.py:178`

问题：

private KB 搜索时给 `MAX_SECURITY_LEVEL`，文档列表/读取也不按用户 `security_level` 过滤。若 `security_level` 表示企业密级，低级别 private KB 成员可以读取高级别内容。

影响：

- 可能形成对象权限与密级权限冲突。
- 安全语义容易被误解。

建议：

- 明确 `security_level` 的产品含义。
- 如果它表示企业密级，应对所有 visibility 一律强制过滤。
- 如果 private KB 例外是产品决策，要在测试和文档中明确说明。

## P1 / 中优先级问题

### 7. 登录缺少限流、失败审计和 dummy hash

位置：

- `apps/backend/app/services/auth_service.py:50`
- `apps/backend/app/api/routes/auth.py:33`

问题：

不存在用户时直接返回，存在用户时执行 bcrypt，存在时序差异。登录、刷新、登出接口没有限流和失败审计。

建议：

- 对不存在用户执行固定 dummy hash。
- 按 IP + email 做 rate limit。
- 记录失败登录、refresh 失败和异常登出事件。

### 8. 生产环境判断没有 `strip()`

位置：

- `apps/backend/app/core/config.py:74`

问题：

生产环境判断使用 `settings.app_env.lower()`，如果配置成 `production `，会绕过生产校验。

建议：

- 统一使用 `settings.app_env.strip().lower()`。
- 对未知环境 fail closed。

### 9. 审计写入吞掉所有异常

位置：

- `apps/backend/app/services/audit_service.py:21`

问题：

`record_audit_event()` 捕获所有异常后 rollback 并返回 `None`，关键安全事件可能静默丢失。

建议：

- 至少记录 fallback logger 和 metrics。
- 管理员变更、权限拒绝、删除失败等关键事件可考虑 fail closed 或告警。

### 10. 上传文件全量读入内存后才检查大小

位置：

- `apps/backend/app/api/routes/documents.py:33`
- `apps/backend/app/services/document_service.py:69`

问题：

上传接口先 `await file.read()`，然后才检查大小。大文件或并发上传会先消耗内存。

建议：

- 在反向代理和 ASGI 层设置 request body limit。
- 后端改为流式读取并边读边限制大小。
- 对上传请求增加并发限制。

### 11. 文档去重只有应用层查询

位置：

- `apps/backend/app/services/document_service.py:76`
- `apps/backend/app/db/models/document.py:24`

问题：

上传时先查询 `content_hash`，没有 `(knowledge_base_id, content_hash)` 唯一约束。并发上传同一内容可能双写。

建议：

- 增加数据库唯一约束或部分唯一约束。
- 捕获 IntegrityError 并返回 409。

### 12. 索引任务缺少文档级锁和幂等 guard

位置：

- `apps/backend/app/workers/document_tasks.py:20`
- `apps/backend/app/workers/document_tasks.py:53`

问题：

重复 Celery 任务可能同时处理同一文档，交错删除/插入 chunk 和向量，造成重复 chunk、过期向量或状态覆盖。

建议：

- 为文档处理引入 processing lease / job id。
- 状态更新使用条件更新。
- 向量 upsert/delete 使用幂等 point id 和任务版本。

### 13. 无检索结果仍调用 LLM

位置：

- `apps/backend/app/rag/answering.py:49`
- `apps/backend/app/llm/provider.py:412`

问题：

无 chunk 时仍调用 LLM，拒答依赖 prompt。企业 RAG 应尽量减少幻觉和不必要成本。

建议：

- 服务层在 `used_chunks=[]` 时直接返回确定性拒答。
- 记录 retrieval log 和 audit event，但不调用 LLM。

### 14. summary/writing 二次生成存在引用漂移

位置：

- `apps/backend/app/agents/summary_agent.py:11`
- `apps/backend/app/agents/writing_agent.py:12`

问题：

先生成 RAG 答案，再让第二次 LLM 总结/写作，但最终仍沿用第一次 RAG 的 citations。用户可能误以为二次输出逐句受引用支撑。

建议：

- 二次生成输出需重新对齐引用。
- 或明确标记“基于已检索答案改写”，不要展示为逐句证据引用。
- 对企业知识问答可优先返回结构化 grounded answer，而不是自由二次改写。

### 15. 失败的 LLM 调用不会落库

位置：

- `apps/backend/app/llm/provider.py:489`
- `apps/backend/app/services/llm_log_service.py:12`

问题：

provider 异常直接抛 `RuntimeError`，`llm_log_service` 只记录成功返回的 `LlmCompletion`。

建议：

- 增加失败 LLM 日志模型或工厂方法。
- 记录 provider、model、latency、error type、agent_name、conversation_id。

### 16. 前端 token 存在 localStorage

位置：

- `apps/frontend/src/stores/authStore.ts:8`

问题：

XSS 后 refresh token 可被直接读取。

建议：

- 企业生产改为 httpOnly/SameSite/Secure cookie。
- 或采用 BFF/session 模式。
- 增加 CSP、依赖安全扫描和 XSS 防护。

## P2 / 设计与企业级改进

### 17. 当前 Compose/Dockerfile 是开发态部署

位置：

- `infra/docker-compose.yml:73`
- `apps/frontend/Dockerfile:12`

问题：

后端使用 `--reload` 和 bind mount，前端使用 Vite dev server。Qdrant/MinIO 使用 `latest`，多个内部服务端口直接暴露到宿主机。

建议：

- 拆分 dev/prod profile。
- 生产前端使用静态构建 + Nginx/CDN。
- 后端禁 reload，去 bind mount，使用非 root 用户。
- 固定镜像 tag/digest。
- 生产只暴露必要入口，内部服务走私有网络。

### 18. BM25 检索大规模性能风险

位置：

- `apps/backend/app/rag/advanced_retrieval.py:304`
- `apps/backend/app/rag/advanced_retrieval.py:348`

问题：

BM25 使用数据库 `ILIKE '%term%'` 拉取候选，再 Python 打分。大知识库会快速退化。

建议：

- PostgreSQL FTS、OpenSearch、Elasticsearch 或专用稀疏检索引擎。
- 对候选数设置硬上限。
- 增加查询耗时、命中数量和降级指标。

### 19. 文件准入只看扩展名

位置：

- `apps/backend/app/services/document_service.py:23`
- `apps/backend/app/services/document_service.py:61`

问题：

上传仅按文件名后缀判断，缺少 MIME magic sniffing、恶意文件扫描、解析沙箱和超时。

建议：

- 内容类型识别。
- AV 扫描。
- 解析任务资源限制。
- 对 PDF/DOCX parser 设置超时和异常隔离。

### 20. Qdrant payload index 创建失败被静默忽略

位置：

- `apps/backend/app/rag/vector_store.py:37`

问题：

`ensure_payload_indexes()` 捕获 `RuntimeError` 后继续，过滤性能或安全相关索引失败可能长期不可见。

建议：

- 结构化日志和告警。
- 健康检查暴露索引状态。
- 生产启动时关键索引失败应 fail fast。

### 21. Memory editor 可能从 assistant answer 沉淀长期记忆

位置：

- `apps/backend/app/services/memory_service.py:418`
- `apps/backend/app/services/memory_service.py:353`

问题：

`memory_source_text()` 把 User 和 Assistant 都拼进 source_text。虽然 prompt 要求只保存用户话语支持的事实，但代码没有验证 evidence 必须来自用户消息。

建议：

- evidence 必须是 user message 的子串或结构化引用。
- 禁止从企业回答沉淀用户长期记忆。
- 高敏感记忆默认 pending。

### 22. 前端 API 错误处理不够稳健

位置：

- `apps/frontend/src/lib/api.ts:812`
- `apps/frontend/src/lib/api.ts:857`

问题：

FastAPI 422 的 `detail` 常为 list，当前只处理 string；SSE JSON parse 没有降级保护。

建议：

- 统一后端错误模型。
- 前端格式化 Pydantic validation errors。
- SSE 坏帧应局部报错，而不是直接中断整个 UI 状态。

### 23. Chat 页面单个知识库选择与后端能力不一致

位置：

- `apps/frontend/src/pages/ChatPage.tsx:64`
- `apps/backend/app/services/knowledge_base_service.py:318`

问题：

前端“单个知识库”只允许个人 private KB；后端 single scope 可以校验任意可访问 KB。用户无法在聊天页单独指定 public/department KB，只能聚合检索。

建议：

- 前端单个知识库下拉展示所有可访问 KB。
- 用标签区分 private/department/public。

### 24. 模型层缺少枚举和范围约束

位置：

- `apps/backend/app/db/models/user.py:21`
- `apps/backend/app/db/models/knowledge_base.py:20`
- `apps/backend/app/db/models/knowledge_base.py:38`

问题：

`security_level`、`visibility`、member `role` 主要靠服务层/schema 校验。绕过服务写库后可能造成权限语义漂移。

建议：

- 增加 `CheckConstraint`。
- 增加迁移和脏数据检测脚本。

## 建议改造路线

### 第一阶段：先修真实 bug 和安全边界

1. 修复 refresh token 原子轮换和前端 logout token 不同步。
2. 移除公开注册自动管理员，改 bootstrap 流程。
3. 修复多知识库日志/Agent run 的权限复核。
4. 明确并修正 private KB 与 `security_level` 的语义。
5. 给登录/refresh 加限流、失败审计和 dummy hash。

### 第二阶段：修一致性和可靠性

1. 删除流程改为软删除 + outbox 清理。
2. 文档上传加唯一约束。
3. Celery 索引任务加 job lease 和幂等保护。
4. 无检索结果直接确定性拒答。
5. 失败 LLM 调用入库。

### 第三阶段：生产硬化

1. 拆 dev/prod compose。
2. 前端静态构建，后端禁 reload。
3. 固定镜像版本，最小暴露端口。
4. 密钥进入 secret manager，不使用本地 `.env` 作为生产凭据。
5. 完善 CSP、cookie session、日志、metrics、alert。

### 第四阶段：顶级项目质量

1. 前端加入 lint、组件测试、API contract/mock 测试。
2. 后端加入并发测试、权限回归测试、迁移 schema diff 强校验。
3. RAG 加离线评测、召回质量指标、引用覆盖率指标。
4. 增加数据保留、审计导出、权限变更影响分析。
5. 建立 threat model 和安全回归清单。

