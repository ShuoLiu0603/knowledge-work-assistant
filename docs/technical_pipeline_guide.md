# Agentic RAG 企业知识库助手技术文档

这份文档按当前项目代码编写，目标是让第一次接触后端、前端、RAG、Agent 的同学也能看懂。

你可以先记住一句话：

```text
用户上传文档
-> 后端保存原文件
-> Worker 把文档解析成文本片段
-> 文本片段生成向量并写入 Qdrant
-> 用户提问
-> 系统检索相关片段
-> LLM 只基于这些片段生成带引用的回答
```

项目不是一个简单聊天机器人，而是一个“企业知识库问答系统”。它重点解决的问题是：企业制度、报销规则、流程说明等文档很多，用户不想全文搜索和自己翻文档，就让系统根据已入库的文档回答，并告诉用户答案来自哪些文档片段。

## 1. 项目全局总览

### 1.1 项目做什么

当前项目实现了这些核心能力：

- 用户注册、登录、JWT 鉴权。
- 第一个注册用户自动成为管理员。
- 用户安全等级 `security_level`，范围是 L1 到 L5。
- 私有知识库、部门知识库和公司公开知识库。
- 文档上传、去重、异步解析、切分、Embedding、向量入库。
- RAG 问答，返回答案和引用。
- SSE 流式聊天。
- Agent 编排：记忆加载、意图判断、RAG/总结/写作路由、记忆更新。
- 短期记忆和长期记忆。
- 检索日志、LLM 调用日志、Agent 运行 trace、审计日志。
- 管理端指标和用户安全等级管理。
- 本地 Docker Compose 一键启动。
- 测试脚本和端到端 smoke demo。

### 1.2 最重要的两条 pipeline

第一条是“文档入库 pipeline”：

```text
浏览器选择文件
-> 前端提交 multipart/form-data
-> FastAPI 接口接收文件
-> 校验权限、文件类型、文件大小、重复内容
-> 原文件上传到 MinIO
-> PostgreSQL 创建 documents 记录
-> Celery 投递 process_document(document_id)
-> Worker 从 MinIO 下载原文件
-> 解析 PDF/DOCX/TXT/MD/CSV
-> 切分成 chunks
-> PostgreSQL 写 document_chunks
-> Embedding provider 生成向量
-> Qdrant 写向量点和 payload
-> documents.status 变成 indexed
```

第二条是“提问回答 pipeline”：

```text
用户输入问题
-> 前端调用问答接口或流式聊天接口
-> 后端校验知识库访问权限
-> 根据检索范围、知识库类型、部门归属和用户清级计算可见内容
-> 构造记忆上下文
-> Query Planning 保留原始问题并拆分必要 sub-queries
-> Dense Retrieval 从 Qdrant 找语义相近片段
-> BM25 Retrieval 从数据库正文找词项匹配片段
-> RRF 按 chunk id 去重并融合多路候选
-> Context Compression 压缩过长片段
-> 生成带编号的上下文
-> LLM 基于上下文回答
-> 返回 answer、citations、retrieval_log
-> 记录 LLM 日志、检索日志、审计日志
```

## 2. 目录结构怎么读

根目录下最关键的目录如下：

```text
apps/
  backend/                  后端 FastAPI 项目
    app/
      api/                  API 路由入口
      agents/               Agent 编排节点
      core/                 配置、安全、健康检查
      db/                   SQLAlchemy 数据库模型和 session
      evaluation/           RAG 评估指标
      llm/                  LLM provider
      rag/                  文档解析、切分、检索、回答
      schemas/              Pydantic 请求/响应模型
      services/             业务流程层
      storage/              MinIO 客户端
      workers/              Celery worker 和任务
    tests/                  后端测试
    alembic/                数据库迁移

  frontend/                 前端 React + TypeScript + Vite 项目
    src/
      pages/                页面
      lib/api.ts            前端请求封装
      stores/               前端状态
      styles/               全局样式

infra/
  docker-compose.yml        本地容器编排

docs/                       项目文档
demo/                       演示文档和评估问题
scripts/                    检查、冒烟、评估脚本
plan/                       原始实施计划
```

如果你是小白，建议按这个顺序读代码：

```text
README.md
-> infra/docker-compose.yml
-> apps/backend/app/main.py
-> apps/backend/app/api/router.py
-> apps/backend/app/services/document_service.py
-> apps/backend/app/workers/document_tasks.py
-> apps/backend/app/services/qa_service.py
-> apps/backend/app/rag/advanced_retrieval.py
-> apps/backend/app/services/conversation_service.py
-> apps/backend/app/agents/graph.py
-> apps/frontend/src/lib/api.ts
```

## 3. Docker 服务说明

本地启动命令：

```bash
docker compose -f infra/docker-compose.yml --env-file .env.example up --build
```

这个命令会启动 7 个服务：

| 服务 | 端口 | 作用 |
|---|---:|---|
| `frontend` | 5173 | React 前端页面 |
| `backend` | 8000 | FastAPI 后端 API |
| `worker` | 无外部端口 | Celery 文档处理 Worker |
| `postgres` | 5432 | 主业务数据库 |
| `redis` | 6379 | Celery 队列和短期记忆 |
| `qdrant` | 6333/6334 | 向量数据库 |
| `minio` | 9000/9001 | 对象存储，保存原始上传文件 |

### 3.1 为什么要这些服务

`frontend` 是用户看到的网页。

`backend` 负责登录、权限、上传接口、问答接口、日志接口。

`worker` 专门处理耗时任务，比如解析 PDF、生成向量。这样用户上传文件后，后端接口可以很快返回，不用一直卡住浏览器。

`postgres` 保存结构化数据，比如用户、知识库、文档记录、chunk、会话、日志。

`redis` 有两个作用：一是 Celery 的任务队列，二是保存短期对话记忆。

`qdrant` 保存向量。向量可以理解为“文本的数学坐标”，相似文本的坐标会更接近。

`minio` 保存原始文件。数据库不直接存 PDF/DOCX 文件本体，只存它在 MinIO 里的 `object_key`。

## 4. 环境变量说明

环境变量样例在 `.env.example`。

### 4.1 基础配置

```text
APP_ENV=development
APP_NAME=agentic-rag-platform
API_PREFIX=/api
BACKEND_CORS_ORIGINS=...
VITE_API_BASE_URL=http://localhost:8000/api
```

含义：

- `APP_ENV`：运行环境。生产环境会做更严格的安全检查。
- `APP_NAME`：FastAPI 应用标题。
- `API_PREFIX`：后端接口统一前缀，当前是 `/api`。
- `BACKEND_CORS_ORIGINS`：允许哪些前端域名访问后端。
- `VITE_API_BASE_URL`：前端请求后端的地址。

### 4.2 数据服务

```text
DATABASE_URL=postgresql+psycopg://rag_user:rag_password@postgres:5432/rag_app
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=knowledge_chunks
MINIO_ENDPOINT=minio:9000
MINIO_BUCKET=documents
```

含义：

- `DATABASE_URL`：后端连接 PostgreSQL 的地址。
- `REDIS_URL`：后端和 Celery 连接 Redis 的地址。
- `QDRANT_URL`：向量库地址。
- `QDRANT_COLLECTION`：Qdrant collection 名称。
- `MINIO_ENDPOINT`：对象存储地址。
- `MINIO_BUCKET`：保存文档的 bucket 名。

### 4.3 模型配置

```text
LLM_PROVIDER=openai_compatible
LLM_MODEL=gpt-4o-mini
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=384
```

模型配置必须指向真实服务：

- `LLM_PROVIDER=openai_compatible`：使用兼容 OpenAI Chat Completions 的 LLM 服务。
- `EMBEDDING_PROVIDER=openai_compatible`：使用兼容 OpenAI Embeddings 的向量服务。

缺少 API key、依赖缺失或模型服务连接失败时，请直接修正配置或服务状态；系统不会降级到本地规则回答或本地 hash embedding。

### 4.4 RAG 参数

```text
DEFAULT_CHUNK_SIZE=800
DEFAULT_CHUNK_OVERLAP=120
RETRIEVAL_TOP_K=5
RETRIEVAL_ROUTE_LIMIT=8
RRF_K=60
CONTEXT_COMPRESSION_CHUNK_CHARS=700
ANSWER_CONTEXT_MAX_CHARS=4000
MAX_UPLOAD_SIZE_MB=50
```

含义：

- `DEFAULT_CHUNK_SIZE`：每个文本片段大约多长。
- `DEFAULT_CHUNK_OVERLAP`：相邻 chunk 重叠多少字符，避免边界处信息断裂。
- `RETRIEVAL_TOP_K`：最终默认选几个片段给 LLM。
- `RETRIEVAL_ROUTE_LIMIT`：每个 query、每条检索路线召回的候选数量。
- `RRF_K`：RRF 融合排序参数。
- `CONTEXT_COMPRESSION_CHUNK_CHARS`：每个入选 chunk 最多保留多少字符。
- `ANSWER_CONTEXT_MAX_CHARS`：给 LLM 的总上下文最大长度。
- `MAX_UPLOAD_SIZE_MB`：单文件最大上传大小。

## 5. 后端启动 pipeline

后端入口是 `apps/backend/app/main.py`。

启动流程：

```text
uvicorn app.main:app
-> create_app()
-> get_settings() 读取环境变量
-> validate_runtime_settings() 校验生产环境安全配置
-> FastAPI 创建 app
-> 配置 CORS
-> include_router(api_router, prefix="/api")
-> lifespan 启动
-> init_db()
-> ensure_qdrant_collection()
```

### 5.1 `init_db()` 做什么

文件：`apps/backend/app/db/session.py`

开发环境下，如果 `AUTO_CREATE_TABLES=true`：

```text
导入所有 db models
-> Base.metadata.create_all()
-> ensure_runtime_columns()
```

这让本地演示更方便：第一次启动时会自动创建表。

生产环境建议：

```text
AUTO_CREATE_TABLES=false
```

然后用 Alembic：

```bash
cd apps/backend
alembic upgrade head
```

### 5.2 `ensure_qdrant_collection()` 做什么

文件：`apps/backend/app/rag/vector_store.py`

启动时后端会尝试连接 Qdrant：

```text
检查 collection 是否存在
-> 不存在就创建 knowledge_chunks
-> 设置向量维度和 Cosine 距离
-> 创建 payload indexes
```

payload index 包括：

- `user_id`
- `knowledge_base_id`
- `document_id`
- `file_name`
- `security_level`

这些字段用于检索时快速过滤，不让用户搜到不该看的文档。

## 6. API 路由总览

路由聚合文件是 `apps/backend/app/api/router.py`。

当前主要 API：

| 模块 | 路径 | 作用 |
|---|---|---|
| Auth | `/api/auth/register` | 注册 |
| Auth | `/api/auth/login` | 登录 |
| Auth | `/api/auth/refresh` | 刷新 token |
| Auth | `/api/auth/logout` | 退出 |
| Auth | `/api/me` | 获取当前用户 |
| Health | `/api/health` | 健康检查 |
| Health | `/api/ready` | 依赖就绪检查 |
| Knowledge Base | `/api/knowledge-bases` | 知识库 CRUD |
| Documents | `/api/knowledge-bases/{kb_id}/documents` | 上传和列表 |
| Documents | `/api/documents/{document_id}` | 文档详情和删除 |
| Documents | `/api/documents/{document_id}/chunks` | 查看 chunk |
| QA | `/api/knowledge-bases/{kb_id}/ask` | 非流式问答 |
| Conversations | `/api/conversations` | 会话管理 |
| Conversations | `/api/conversations/{id}/messages/stream` | SSE 流式聊天 |
| Agent Runs | `/api/agent-runs` | Agent 运行记录 |
| Memories | `/api/memories` | 长期记忆管理 |
| Retrieval Logs | `/api/retrieval-logs` | 检索日志 |
| LLM Logs | `/api/llm-logs` | LLM 调用日志 |
| Feedbacks | `/api/feedbacks` | 点赞点踩 |
| Admin | `/api/admin/metrics` | 管理指标 |
| Admin | `/api/admin/users` | 用户管理 |
| Admin | `/api/admin/audit-logs` | 审计日志 |

## 7. 用户注册和登录 pipeline

核心文件：

- `apps/backend/app/api/routes/auth.py`
- `apps/backend/app/services/auth_service.py`
- `apps/backend/app/core/security.py`
- `apps/backend/app/db/models/user.py`
- `apps/backend/app/db/models/refresh_token.py`

### 7.1 注册流程

```text
前端提交 email、username、password
-> POST /api/auth/register
-> 检查 email 是否已存在
-> 判断是不是第一个用户
-> 密码 hash
-> 创建 users 记录
-> 创建 access token
-> 创建 refresh token
-> refresh token hash 后保存到 refresh_tokens
-> 返回 access_token、refresh_token、user
```

关键规则：

- 第一个注册用户自动成为管理员。
- 第一个用户 `is_admin=true`，`security_level=5`。
- 后续用户默认普通用户，`security_level=1`。
- 数据库不保存明文密码。
- refresh token 不直接明文入库，而是保存 hash。

### 7.2 登录流程

```text
前端提交 email、password
-> POST /api/auth/login
-> 根据 email 查用户
-> 校验密码 hash
-> 检查用户是否 active
-> 签发 access token 和 refresh token
-> 返回给前端
```

### 7.3 前端如何保存登录状态

前端请求封装在 `apps/frontend/src/lib/api.ts`。

登录后会得到：

```json
{
  "access_token": "jwt",
  "refresh_token": "random-token",
  "token_type": "bearer",
  "user": {
    "id": "...",
    "email": "...",
    "username": "...",
    "is_admin": true,
    "security_level": 5
  }
}
```

后续访问受保护接口时，前端会带：

```text
Authorization: Bearer <access_token>
```

## 8. 知识库和权限 pipeline

核心文件：

- `apps/backend/app/services/knowledge_base_service.py`
- `apps/backend/app/db/models/knowledge_base.py`
- `apps/backend/app/schemas/knowledge_base.py`

### 8.1 知识库类型

当前支持两种：

| 类型 | visibility | 说明 |
|---|---|---|
| 私有知识库 | `private` | 普通用户可以创建，默认 owner 可管理 |
| 部门知识库 | `department` | 同部门可读，owner/editor 或管理员维护文档 |
| 公开知识库 | `public` | 只有管理员可以创建和发布 |

### 8.2 成员角色

`knowledge_base_members` 表里保存用户在知识库中的角色：

| 角色 | 说明 |
|---|---|
| `owner` | 拥有者，最高权限 |
| `editor` | 可编辑，能上传/管理私有或部门知识库文档 |
| `viewer` | 只读 |

创建知识库时，系统会自动创建一条 owner 成员记录。

### 8.3 创建知识库流程

```text
POST /api/knowledge-bases
-> 校验 name、description、visibility
-> 如果 visibility=public，检查当前用户是不是 admin
-> 创建 knowledge_bases
-> 创建 knowledge_base_members，role=owner
-> 返回 KnowledgeBaseRead
```

### 8.4 访问控制怎么判断

核心函数是 `ensure_kb_access()`。

它做的事情：

```text
查 knowledge_bases
-> 查当前用户 membership
-> 如果是 public，允许已登录用户 viewer 访问
-> 如果是 private，必须是成员
-> 如果要求 editor/owner，检查角色等级是否足够
-> 不满足则返回 404 或 403
```

小白可以这样理解：

- 不是你的私人知识库，看不到。
- 部门知识库同部门用户可读，但管理文档需要 owner/editor 或管理员。
- 公开知识库所有登录用户可读，但管理文档需要管理员。
- 管理员对公开知识库有更高权限。

### 8.5 安全等级 `security_level`

用户和文档都有安全等级。

```text
L1 最低
L5 最高
```

公开知识库中：

```text
用户只能看到 security_level <= 自己等级 的文档和 chunk
```

私人知识库中：

```text
只按成员权限隔离，不按文档密级筛选
```

当前实现中，私人知识库上传时会忽略前端传来的密级，统一使用默认等级。

## 9. 文档上传 pipeline

核心文件：

- `apps/frontend/src/lib/api.ts`
- `apps/backend/app/api/routes/documents.py`
- `apps/backend/app/services/document_service.py`
- `apps/backend/app/storage/minio_client.py`
- `apps/backend/app/workers/document_tasks.py`

### 9.1 前端上传发生了什么

前端调用：

```ts
uploadDocument(token, kbId, file, securityLevel)
```

它会构造 `FormData`：

```text
file=<用户选择的文件>
security_level=<文档密级>
```

然后请求：

```text
POST /api/knowledge-bases/{kb_id}/documents
```

### 9.2 后端上传接口做什么

进入 `create_uploaded_document()`。

完整流程：

```text
根据 user_id 查当前用户
-> 检查用户是否能管理该知识库文档
-> 计算本次上传的 security_level
-> 清理文件名，避免危险字符
-> 检查扩展名
-> 检查文件大小
-> 计算 SHA256 content_hash
-> 检查同一个知识库中是否已有相同 content_hash
-> 创建 documents 记录，status=uploaded，object_key=pending
-> flush 拿到 document.id
-> 生成 MinIO object_key
-> 上传原始 bytes 到 MinIO
-> 更新 document.object_key
-> commit
-> Celery 投递 process_document(document.id)
-> 写 audit_log
-> 返回 document_id、status、job_id、security_level
```

支持的文件类型：

```text
pdf, docx, txt, md, csv
```

默认大小限制：

```text
MAX_UPLOAD_SIZE_MB=50
```

### 9.3 文件名怎么处理

后端会调用 `sanitize_file_name()`。

它会：

- 只保留安全字符。
- 保留中文、英文、数字、点、下划线、短横线。
- 去掉路径部分，避免用户传入类似 `../../xxx` 的危险路径。

### 9.4 MinIO object key 长什么样

当前格式：

```text
knowledge-bases/{kb_id}/documents/{document.id}/{safe_name}
```

例如：

```text
knowledge-bases/abc/documents/def/company_policy_demo.md
```

数据库 `documents.object_key` 保存这个路径。

### 9.5 为什么要 content_hash 去重

系统会对文件 bytes 做 SHA256：

```text
content_hash = sha256(file_bytes)
```

如果同一个知识库里已经有相同 hash 的文档，说明内容重复，直接返回冲突错误。

这样可以避免：

- 重复占用 MinIO 存储。
- 重复生成 chunk。
- 重复写 Qdrant 向量。
- 检索时重复命中同一份资料。

## 10. 文档入库 Worker pipeline

核心文件：`apps/backend/app/workers/document_tasks.py`

Celery 任务名：

```text
process_document
```

状态流转：

```text
uploaded
-> parsing
-> chunking
-> embedding
-> indexed
```

失败时：

```text
failed
```

### 10.1 Worker 为什么要单独存在

解析文档和生成向量可能比较慢。

如果在上传接口里同步做这些事，用户上传大文件时浏览器可能等待很久，甚至超时。

所以当前设计是：

```text
上传接口只负责保存原文件和投递任务
Worker 在后台慢慢解析和入库
前端轮询文档状态
```

### 10.2 Worker 详细流程

```text
Worker 收到 document_id
-> init_db()
-> 查询 documents
-> status=parsing
-> 从 MinIO 根据 object_key 下载原文件 bytes
-> parse_document()
-> 如果没有提取出文本，失败
-> status=chunking
-> split_blocks()
-> 如果没有生成 chunk，失败
-> status=embedding
-> 删除旧 document_chunks
-> 删除旧 Qdrant vectors
-> 创建新的 DocumentChunk ORM 对象
-> flush，拿到 chunk id 和 qdrant_point_id
-> upsert_document_chunks()
-> status=indexed
-> chunk_count=len(chunks)
-> commit
```

### 10.3 解析器做什么

核心文件：`apps/backend/app/rag/loaders.py`

入口：

```python
parse_document(file_bytes, file_name, file_ext)
```

不同文件使用不同解析逻辑：

| 文件类型 | 解析方式 |
|---|---|
| PDF | 逐页提取文本，保留页码 |
| DOCX | 读取段落和表格，识别标题层级 |
| TXT | 解码纯文本 |
| MD | 解析 Markdown 标题路径 |
| CSV | 读取表格并转成文本行 |

解析结果不是直接一整段字符串，而是多个 `ParsedBlock`。

你可以把 `ParsedBlock` 理解为：

```text
一小块带元数据的原始文本
```

常见元数据：

- `page_number`：页码。
- `title_path`：标题路径。
- `section_name`：章节名。
- `metadata`：额外信息。

### 10.4 切分器做什么

核心文件：`apps/backend/app/rag/splitters.py`

入口：

```python
split_blocks(blocks, chunk_size, chunk_overlap)
```

为什么要切分？

因为一份文档可能很长，不能全部塞给向量数据库或 LLM。系统要把长文档切成较短片段。

切分后的 `TextChunk` 包含：

- `content`：片段正文。
- `token_count`：估算 token 数。
- `title_path`：继承自原始 block。
- `page_number`：继承自原始 block。
- `section_name`：继承自原始 block。
- `metadata`：额外信息。

### 10.5 PostgreSQL 里保存什么

`documents` 保存文档级信息：

- 文件名。
- 文件大小。
- MinIO object key。
- content hash。
- status。
- chunk_count。
- security_level。

`document_chunks` 保存片段级信息：

- chunk 内容。
- chunk 顺序。
- token_count。
- title_path。
- page_number。
- section_name。
- qdrant_point_id。
- security_level。
- metadata。

### 10.6 Qdrant 里保存什么

每个 chunk 会写入一个 Qdrant point。

point 包含：

```json
{
  "id": "chunk.qdrant_point_id",
  "vector": [0.1, 0.2, "..."],
  "payload": {
    "user_id": "knowledge_base.owner_id",
    "knowledge_base_id": "kb_id",
    "document_id": "doc_id",
    "chunk_id": "chunk_id",
    "chunk_index": 0,
    "content": "chunk text",
    "file_name": "company_policy_demo.md",
    "file_ext": "md",
    "security_level": 1,
    "title_path": "...",
    "page_number": null,
    "section_name": "...",
    "metadata": {}
  }
}
```

注意：Qdrant payload 里也保存了 `content`。这样 dense 检索命中后，可以直接拿到文本和引用信息。

### 10.7 失败时如何处理

如果解析、切分、Embedding 或 Qdrant 写入任何一步失败：

```text
rollback
-> 重新查询 document
-> status=failed
-> error_message=str(exc)
-> chunk_count=0
-> 尝试删除 Qdrant 中该 document 的向量
-> 返回失败状态
```

前端文档列表会显示失败状态和错误信息。

## 11. RAG 检索 pipeline

核心文件：

- `apps/backend/app/services/qa_service.py`
- `apps/backend/app/rag/advanced_retrieval.py`
- `apps/backend/app/rag/retrieval.py`
- `apps/backend/app/rag/vector_store.py`

### 11.1 问答入口

非流式问答接口：

```text
POST /api/knowledge-bases/{kb_id}/ask
```

请求：

```json
{
  "question": "住宿报销上限是多少？",
  "top_k": 5
}
```

响应：

```json
{
  "question": "住宿报销上限是多少？",
  "answer": "根据知识库中检索到的内容...",
  "citations": [
    {
      "chunk_id": "...",
      "document_id": "...",
      "file_name": "company_policy_demo.md",
      "chunk_index": 2,
      "score": 0.82,
      "content_preview": "...",
      "title_path": "...",
      "page_number": null,
      "section_name": "...",
      "security_level": 1,
      "rrf_score": 0.016,
      "retrieval_routes": ["dense_original", "bm25_original"]
    }
  ],
  "retrieval_log": {
    "id": "...",
    "question": "住宿报销上限是多少？",
    "rewritten_query": "住宿报销上限是多少",
    "retrieval_routes": ["dense_original", "bm25_original"]
  }
}
```

### 11.2 权限和安全等级过滤

问答时先执行：

```text
ensure_kb_access(..., required_role="viewer")
```

然后计算 `max_security_level`：

```text
如果知识库是 private:
    max_security_level = MAX_SECURITY_LEVEL
如果知识库是 public:
    max_security_level = 当前用户 security_level
```

这意味着：

- 私人知识库：成员可读范围内全部可检索。
- 公开/部门知识库：只能检索不高于当前用户清级的文档片段。
- 检索范围：`single` 只查当前库；`department` 查本部门库和公司公开库；`accessible` 查当前用户全部可访问库。

Dense 和 BM25 两条路线都会带上这个等级过滤。

### 11.3 Query Planning

函数：

```python
plan_retrieval_queries(question)
```

作用：

- 保留原始问题作为第一检索 query。
- 归一化问题，用于日志和问题拆分。
- 必要时拆分最多 3 个 sub-queries。

例子：

```text
请问住宿报销上限是多少？
-> 住宿报销上限是多少
```

这样可以避免改写丢失用户原意，同时让复杂问题获得更完整的召回。

### 11.4 Sub-query Decomposition

函数：

```python
decompose_question(normalized_query)
```

作用：把复杂问题拆成子问题。

例子：

```text
住宿和交通分别怎么报销？
-> 住宿怎么报销
-> 交通怎么报销
```

当前实现使用规则拆分，识别：

- 中文连接词：以及、并且、同时、分别、对比、比较、和。
- 标点：逗号、分号、问号等。
- 英文连接词：and、or、with、vs。

### 11.5 Retrieval Query Set

最终检索 query 集合只包含：

```text
原始问题
最多 3 个 sub-queries
```

系统不做关键词扩展，不做 metadata query。这样链路更短，检索日志也更容易解释。

### 11.6 Dense Retrieval

Dense Retrieval 使用 Qdrant。

流程：

```text
query -> embedding vector
-> Qdrant points/search
-> filter user_id
-> filter knowledge_base_id
-> filter security_level <= max_security_level
-> 返回相似 chunk
```

Qdrant filter 里最关键的是：

```text
user_id = knowledge_base.owner_id
knowledge_base_id = 当前知识库
security_level <= 当前可见等级
```

Dense Retrieval 擅长找“语义相似”的内容，即使关键词不完全一样也可能命中。

### 11.7 BM25 Retrieval

BM25 Retrieval 使用数据库正文做词项召回。系统先按 query terms 预过滤候选 chunk，再用 BM25 公式计算排序分数。

BM25 擅长找精确词，比如：

- 报销。
- 发票。
- 住宿。
- 某个制度名称。

### 11.8 RRF 融合

RRF 是 Reciprocal Rank Fusion。

你可以简单理解为：

```text
一个 chunk 如果被多条检索路线都排在前面，它更可能重要。
```

当前公式核心：

```text
rrf_score = sum(route_weight / (rrf_k + rank))
```

其中 `rank` 是候选在某条 route 里的排名。原始问题路线权重略高于 sub-query 路线，避免拆分问题反客为主。

### 11.9 Context Compression

函数：

```python
compress_selected_chunks()
```

作用：把太长的 chunk 压缩到设定长度。

默认：

```text
CONTEXT_COMPRESSION_CHUNK_CHARS=700
```

压缩逻辑会优先保留包含查询词的句子。这样给 LLM 的上下文更短、更集中。

## 12. 答案生成 pipeline

核心文件：

- `apps/backend/app/rag/answering.py`
- `apps/backend/app/llm/provider.py`
- `apps/backend/app/services/llm_log_service.py`

### 12.1 构造上下文

系统会把入选 chunks 变成带编号的知识上下文：

```text
[1] 文件: company_policy_demo.md
章节: 差旅报销 / 住宿标准
内容: ...

[2] 文件: company_policy_demo.md
章节: 差旅报销 / 发票要求
内容: ...
```

这些编号会成为引用标记。

### 12.2 Prompt 的原则

LLM system prompt 的核心要求：

```text
You are an enterprise knowledge assistant.
Answer only from the provided context.
Use citation markers such as [1].
Use memory only to understand user style, never as knowledge evidence.
Say when the knowledge context is insufficient.
```

翻译成中文就是：

- 你是企业知识助手。
- 只能根据给定知识上下文回答。
- 回答中要使用 `[1]` 这样的引用标记。
- 记忆只能用于理解用户偏好，不能当知识库证据。
- 如果上下文不足，要说依据不足。

### 12.3 LLM Provider

当前有两类：

| Provider | 说明 |
|---|---|
| `openai_compatible` | 兼容 OpenAI Chat Completions 的服务 |

默认：

```text
LLM_PROVIDER=openai_compatible
```

如果真实模型配置缺失、依赖缺失或调用失败，系统直接报错，不再 fallback 到本地 provider。

### 12.4 没有检索到内容怎么办

如果没有 selected chunks，回答链路仍然调用 LLM provider，并传入空 `Knowledge context`。Prompt 会约束模型说明当前可访问知识库中没有足够依据，不得用通用知识补答，也不得虚构引用。

回答保存后，系统会按消息数周期性更新会话摘要。摘要更新属于非阻断维护任务：如果摘要 LLM 调用失败，不会把已经生成的 assistant message 改成失败。

### 12.5 记录 LLM 日志

如果调用了 LLM provider，会写入 `llm_call_logs`：

- user_id。
- conversation_id。
- agent_name。
- provider。
- model_name。
- prompt_tokens。
- completion_tokens。
- total_tokens。
- latency_ms。
- status。
- fallback_used（保留历史字段；当前 LLM provider 不再使用本地 fallback）。
- error_message。

## 13. 流式聊天 SSE pipeline

核心文件：

- `apps/backend/app/services/conversation_service.py`
- `apps/backend/app/api/routes/conversations.py`
- `apps/frontend/src/lib/api.ts`
- `apps/frontend/src/pages/ChatPage.tsx`

流式接口：

```text
POST /api/conversations/{conversation_id}/messages/stream
```

请求：

```json
{
  "question": "住宿报销上限是多少？",
  "top_k": 5
}
```

### 13.1 后端流式流程

```text
校验 question 非空
-> get_conversation() 校验会话属于当前用户
-> 如果标题还是“新会话”，用问题生成标题
-> 保存 user message
-> yield conversation 事件
-> yield user_message 事件
-> 写入 Redis 短期记忆
-> yield trace: agent_graph started
-> 开线程运行 Agent
-> Agent 生成 token 时放入 Queue
-> 主线程从 Queue 读取 token
-> yield token 事件
-> Agent 完成后读取 agent_run
-> yield trace: completed/failed
-> yield agent_run
-> yield retrieval_log
-> yield citations
-> 保存 assistant message
-> 绑定 agent_run 和 retrieval_log 到 message
-> assistant 回答写入短期记忆
-> 必要时更新会话 summary
-> yield assistant_message
-> yield done
```

### 13.2 SSE 事件类型

当前前端处理这些事件：

| event | 说明 |
|---|---|
| `conversation` | 会话信息 |
| `user_message` | 用户消息已保存 |
| `trace` | 当前执行阶段 |
| `token` | 回答文本增量 |
| `agent_run` | Agent 运行记录 |
| `retrieval_log` | 检索日志 |
| `citations` | 引用列表 |
| `assistant_message` | 助手消息已保存 |
| `done` | 完成 |
| `error` | 错误 |

### 13.3 为什么后端用线程

`stream_message_response()` 一边要持续向前端发送 SSE，一边要跑 Agent。

所以它把 Agent 放到后台线程：

```text
Thread(target=worker)
```

Agent 每生成一个 token，就通过 `Queue` 传给主生成器。主生成器再把 token 包装成 SSE 发给浏览器。

## 14. Agent pipeline

核心文件：

- `apps/backend/app/agents/graph.py`
- `apps/backend/app/agents/state.py`
- `apps/backend/app/agents/memory_agent.py`
- `apps/backend/app/agents/supervisor.py`
- `apps/backend/app/agents/rag_agent.py`
- `apps/backend/app/agents/summary_agent.py`
- `apps/backend/app/agents/writing_agent.py`
- `apps/backend/app/services/agent_service.py`

### 14.1 Agent 总流程

默认：

```text
Memory Agent
-> Supervisor Agent
-> RAG Agent / Memory Answer / Chat / Summary Agent / Writing Agent
-> Memory Agent
```

具体图：

```text
load_memory
-> supervisor
-> 如果 intent=rag，进入 rag_agent
-> 如果 intent=memory，基于记忆上下文回答，不检索知识库
-> 如果 intent=chat，轻量寒暄，不检索知识库
-> 如果 intent=summary，进入 summary_agent
-> 如果 intent=writing，进入 writing_agent
-> update_memory
-> END
```

### 14.2 LangGraph 和顺序执行

配置：

```text
AGENT_GRAPH_BACKEND=langgraph
```

配置为 `langgraph` 时使用 `StateGraph`。

如果本地没安装 LangGraph，运行会失败并记录错误。需要显式使用顺序执行调试时：

```text
AGENT_GRAPH_BACKEND=sequential
```

### 14.3 AgentState 存什么

`AgentGraphState` 存一次 Agent 运行的关键信息：

- user_id。
- knowledge_base_id。
- conversation_id。
- message_id。
- input。
- top_k。
- intent。
- answer。
- citations。
- retrieval_log_id。
- memory_context。
- memory_actions。
- trace。
- status。
- error_message。

### 14.4 Memory Agent

回答前：

```text
加载短期记忆
-> 加载会话 summary
-> 加载相关长期记忆
-> 拼成 memory_context
```

回答后：

```text
从用户输入里提取稳定偏好
-> create/touch/merge/supersede/pending/ignore
-> 更新 user_memories
```

记忆更新失败不会让已经生成的回答失败；失败会记录到 Agent trace 和 memory_actions，主回答仍然返回。

### 14.5 Supervisor Agent

作用：判断用户意图。

可能的 intent：

| intent | 说明 |
|---|---|
| `rag` | 知识库/企业事实问答 |
| `memory` | 用户询问已保存偏好、资料或当前对话记忆 |
| `chat` | 寒暄、感谢等无需检索的小对话 |
| `summary` | 总结 |
| `writing` | 写作/起草 |

Supervisor 不直接回答问题，只负责路由。LLM 先给出建议标签，服务层再做保守归一化：企业事实问题默认走 RAG，明确询问用户记忆才走 memory，纯寒暄才走 chat。

### 14.6 RAG Agent

RAG Agent 不重新实现检索。

它复用：

```text
qa_service.build_rag_answer()
```

这样直接问答和 Agent 问答走同一套 RAG pipeline，不会出现两套逻辑不一致。

### 14.7 Summary Agent

Summary Agent 会先用 RAG 拿到依据，再生成总结。

它不是凭空总结，而是基于知识库检索结果。

### 14.8 Writing Agent

Writing Agent 同样先检索依据，再生成草稿。

适合类似：

```text
帮我根据制度写一份差旅报销通知
```

### 14.9 Agent Trace

每个节点都会往 trace 里写信息。

这让用户可以知道：

- 加载了多少记忆。
- Supervisor 判断成什么意图。
- 走了哪个 Agent。
- 检索日志 id 是什么。
- 是否发生 fallback。
- 运行是否失败。

## 15. 记忆系统 pipeline

核心文件：

- `apps/backend/app/services/memory_service.py`
- `apps/backend/app/db/models/user_memory.py`
- `apps/backend/app/agents/memory_agent.py`

### 15.1 短期记忆

短期记忆保存在 Redis。

key 格式：

```text
memory:short:{user_id}:{conversation_id}
```

写入时：

```text
LPUSH
-> LTRIM 保留最近 SHORT_MEMORY_MAX_MESSAGES 条
-> EXPIRE 24 小时
```

默认：

```text
SHORT_MEMORY_MAX_MESSAGES=12
```

短期记忆适合保存当前会话最近几轮上下文。

### 15.2 长期记忆

长期记忆保存在 PostgreSQL 的 `user_memories` 表。

它适合保存用户稳定偏好，比如：

```text
用户偏好回答简洁。
用户希望技术方案用表格展示优缺点。
用户偏好中文回答。
```

### 15.3 记忆状态

当前允许：

| status | 说明 |
|---|---|
| `active` | 当前生效 |
| `superseded` | 被新记忆覆盖 |
| `ignored` | 忽略 |

### 15.4 自动记忆写入流程

```text
用户发消息
-> Memory Agent 调用 LLM provider 提取结构化记忆候选
-> 没有可记忆内容则 ignore
-> 低置信度则 ignore
-> 置信度不足或敏感度不低则 pending
-> normalize_memory_content()
-> content_hash=sha256(normalized)
-> 查 active 精确重复
-> 如果精确重复，touch
-> 推断 category
-> 生成 embedding
-> 查同 category 的 active 记忆
-> 如果冲突，supersede
-> 如果语义相似，merge
-> 否则 create
```

### 15.5 memory action

| action | 什么时候发生 |
|---|---|
| `create` | 新偏好，创建长期记忆 |
| `touch` | 完全重复，只更新 touched_count 和 last_touched_at |
| `merge` | 语义相似或同方向偏好，合并到旧记忆 |
| `supersede` | 新偏好和旧偏好冲突，旧记忆变 superseded |
| `pending` | 置信度不足或敏感度不低，等待用户审核 |
| `ignore` | 没有稳定偏好，或用户明确说不要记住 |

### 15.6 记忆不能当知识证据

这是非常重要的设计：

```text
长期记忆可以影响回答风格，但不能当作知识库事实依据。
```

比如用户记忆是：

```text
用户偏好简洁回答
```

那么回答可以更简短。

但如果用户记忆是：

```text
用户上次说住宿标准是 500
```

系统不能把这个当制度依据。真正的制度依据必须来自检索到的知识库 chunk。

## 16. 日志和可观测性 pipeline

项目里有多种日志，它们解决的问题不同。

### 16.1 Retrieval Logs

表：`retrieval_logs`

记录一次检索过程：

- 原始问题。
- rewritten_query。
- sub_questions。
- expanded_queries：兼容字段，当前保存实际参与检索的 query 集合。
- retrieval_routes。
- candidates。
- selected_chunks。
- rrf_k。
- compression_chars_saved。

用途：

- 调试为什么没搜到。
- 查看哪条 route 命中。
- 分析 selected chunk 是否合理。

### 16.2 LLM Call Logs

表：`llm_call_logs`

记录一次 LLM 调用：

- provider。
- model_name。
- token 用量。
- 延迟。
- status。
- fallback_used。
- error_message。

用途：

- 估算成本。
- 发现模型接口失败。
- 分析响应慢在哪里。

### 16.3 Agent Runs

表：`agent_runs`

记录一次 Agent 运行：

- input。
- intent。
- answer。
- citations。
- trace。
- state。
- status。
- error_message。

用途：

- 看 Agent 走了哪个节点。
- 看意图判断是否正确。
- 看 memory actions。

### 16.4 Audit Logs

表：`audit_logs`

记录关键安全事件：

- 文档上传。
- 文档删除。
- RAG 检索。
- 会话删除。
- 用户安全等级调整。
- 权限拒绝事件。

用途：

- 管理员审计。
- 查谁做了什么操作。
- 查安全等级相关事件。

### 16.5 Feedbacks

表：`feedbacks`

记录用户对 assistant message 的点赞/点踩。

用途：

- 计算正反馈率。
- 发现回答质量问题。
- 给后续评估和优化提供数据。

## 17. 数据库模型速查

| 表 | 作用 |
|---|---|
| `users` | 用户、管理员标记、安全等级 |
| `refresh_tokens` | refresh token hash 和过期/撤销状态 |
| `knowledge_bases` | 知识库 |
| `knowledge_base_members` | 知识库成员和角色 |
| `documents` | 上传文档记录 |
| `document_chunks` | 文档切分后的片段 |
| `conversations` | 会话 |
| `messages` | 用户消息和助手消息 |
| `user_memories` | 长期记忆 |
| `agent_runs` | Agent 运行记录 |
| `retrieval_logs` | 检索日志 |
| `llm_call_logs` | LLM 调用日志 |
| `feedbacks` | 点赞点踩 |
| `audit_logs` | 审计日志 |

## 18. 前端页面 pipeline

核心文件：

- `apps/frontend/src/App.tsx`
- `apps/frontend/src/lib/api.ts`
- `apps/frontend/src/pages/LoginPage.tsx`
- `apps/frontend/src/pages/RegisterPage.tsx`
- `apps/frontend/src/pages/KnowledgeBaseListPage.tsx`
- `apps/frontend/src/pages/KnowledgeBaseDetailPage.tsx`
- `apps/frontend/src/pages/ChatPage.tsx`
- `apps/frontend/src/pages/MemoriesPage.tsx`
- `apps/frontend/src/pages/AdminMetricsPage.tsx`

### 18.1 App 启动

前端启动后：

```text
读取本地 auth store
-> 如果有 access token，调用 /api/me
-> token 有效则进入受保护页面
-> token 无效可尝试 refresh
-> 未登录跳转登录页
```

### 18.2 知识库列表页

主要做：

```text
GET /api/knowledge-bases
-> 显示当前用户可见知识库
-> 创建 private/public 知识库
-> 删除知识库
```

普通用户不能创建 public 知识库。

### 18.3 知识库详情页

主要做：

```text
GET /api/knowledge-bases/{id}
-> GET /api/knowledge-bases/{id}/documents
-> 上传文档
-> 显示文档状态
-> 删除文档
-> 查看 chunks
```

公开知识库会显示密级选择。

私人知识库不强调密级，上传时使用默认等级。

### 18.4 聊天页

主要做：

```text
创建或选择 conversation
-> GET messages
-> POST stream
-> 逐 token 渲染回答
-> 展示 citations
-> 展示 retrieval_log
-> 展示 agent_run trace
-> 支持点赞/点踩
```

### 18.5 记忆页

主要做：

```text
GET /api/memories
-> 创建手动记忆
-> 编辑记忆
-> 修改 status
-> 删除记忆
```

### 18.6 管理页

管理员可看：

- 聚合指标。
- 用户列表。
- 用户安全等级。
- LLM 错误。
- 审计日志。

## 19. 从 0 到演示的完整流程

### 19.1 启动

```bash
docker compose -f infra/docker-compose.yml --env-file .env.example up --build
```

打开：

```text
http://localhost:5173
```

### 19.2 注册管理员

注册第一个用户。

第一个用户会自动变成：

```text
is_admin=true
security_level=5
```

### 19.3 创建知识库

建议先创建私人知识库：

```text
名称：企业制度演示知识库
visibility：private
```

如果要测试公开知识库：

```text
必须用管理员账户创建 public
```

### 19.4 上传演示文档

使用：

```text
demo/company_policy_demo.md
```

上传后先看到：

```text
status=uploaded
```

然后 Worker 处理：

```text
parsing -> chunking -> embedding -> indexed
```

等到：

```text
status=indexed
chunk_count > 0
```

就可以提问。

### 19.5 提问

示例：

```text
住宿报销上限是多少？
```

系统会返回：

- 答案。
- 引用。
- 检索日志。
- Agent trace。

### 19.6 查看引用

引用里最重要字段：

- `file_name`：来自哪个文件。
- `chunk_index`：第几个 chunk。
- `content_preview`：片段预览。
- `retrieval_routes`：哪条检索路线命中。
- `rrf_score`：融合排序分数。
- `security_level`：该片段密级。

## 20. 测试和质量门禁

### 20.1 一键检查

```bash
python scripts/check_project.py
```

它会运行：

```text
backend tests
-> python compile
-> migration verification
-> frontend build
-> docker compose config
```

如果服务已经启动，可以加 smoke：

```bash
python scripts/check_project.py --with-smoke
```

### 20.2 Smoke Demo

```bash
python scripts/smoke_demo.py
```

它会自动：

```text
等待后端 ready
-> 注册临时用户
-> 创建 private 知识库
-> 上传 demo/company_policy_demo.md
-> 等待 indexed
-> 查看 chunks
-> 提问
-> 检查 answer 和 citations
-> 默认清理创建的知识库
```

### 20.3 RAG 评估

脚本：

```bash
python scripts/run_eval.py --base-url http://localhost:8000/api --email demo@example.com --password Password123! --kb-id "<knowledge_base_id>" --dataset demo/rag_eval_questions.json --top-k 5
```

指标：

| 指标 | 说明 |
|---|---|
| `Recall@K` | 前 K 个引用是否命中预期来源 |
| `MRR` | 第一个正确引用排得越靠前越好 |
| `citation_hit_rate` | 回答是否给出正确引用 |
| `answer_keyword_hit_rate` | 答案是否包含预期关键词 |

## 21. 常见问题和排障

### 21.1 文档一直不是 indexed

检查：

- Worker 容器是否启动。
- Redis 是否健康。
- 文档状态是否是 `failed`。
- `error_message` 写了什么。
- 文件是否为空或解析不出文本。
- Qdrant 是否能访问。
- Embedding 维度是否和 Qdrant collection 一致。

### 21.2 上传提示重复

原因：

```text
同一个知识库已经存在 content_hash 相同的文件
```

解决：

- 不要重复上传同一文件。
- 删除旧文档后再上传。

### 21.3 问答没有引用

可能原因：

- 文档还没 indexed。
- 问题和文档内容不相关。
- 用户 security_level 太低，看不到公开知识库中的高密级文档。
- 知识库选错。
- 文档解析得到的 chunk 太少或内容质量差。

### 21.4 公开知识库普通用户不能上传

这是当前设计。

公开知识库文档由管理员管理，普通用户只读。

### 21.5 本地没有真实大模型会怎样

当前默认要求真实模型服务：

```text
LLM_PROVIDER=openai_compatible
EMBEDDING_PROVIDER=openai_compatible
```

如果没有配置可用的 LLM key、embedding key 或兼容服务地址，相关请求会直接失败。测试应通过 fake provider patch 调用点，而不是依赖本地兜底。

### 21.6 LangGraph 没安装会怎样

如果配置是：

```text
AGENT_GRAPH_BACKEND=langgraph
```

但环境里没有 LangGraph，Agent run 会失败并记录错误。

需要顺序执行调试时，显式设置 `AGENT_GRAPH_BACKEND=sequential`。

### 21.7 生产环境为什么启动失败

当：

```text
APP_ENV=production
```

后端会拒绝不安全配置，例如：

- 默认 JWT secret。
- SQLite 数据库。
- `AUTO_CREATE_TABLES=true`。
- CORS 允许 `*`。

这是为了避免把开发配置带到生产环境。

## 22. 当前实现边界

这部分很重要，因为计划文档里有些内容是蓝图，当前代码不一定全部实现。

当前边界：

- 不拆微服务，当前是 FastAPI 模块化单体 + Celery Worker。
- 不使用 Kubernetes，本地用 Docker Compose。
- 没有 Elasticsearch，词项检索使用数据库正文预过滤 + BM25 排序。
- 当前知识库 visibility 只有 `private` 和 `public`，没有 `team`。
- 当前公开知识库文档管理仅限管理员。
- 当前没有单独暴露“重新索引文档”接口。
- 当前没有复杂组织架构、审批流、SSO、LDAP。
- OCR 不是主链路能力。
- 默认要求真实 LLM 和 embedding provider；无密钥演示需要在测试或脚本中显式注入 fake provider。

## 23. 一张完整文字版架构图

```text
浏览器 React/Vite
  |
  | HTTP JSON / multipart / SSE
  v
FastAPI Backend
  |
  |-- Auth Service
  |     |-- users
  |     |-- refresh_tokens
  |
  |-- KnowledgeBase Service
  |     |-- knowledge_bases
  |     |-- knowledge_base_members
  |
  |-- Document Service
  |     |-- MinIO 保存原文件
  |     |-- documents 保存文档记录
  |     |-- Celery 投递 process_document
  |
  |-- QA Service
  |     |-- Advanced Retrieval
  |     |-- Answering
  |     |-- retrieval_logs
  |     |-- llm_call_logs
  |
  |-- Conversation Service
  |     |-- conversations
  |     |-- messages
  |     |-- SSE token stream
  |
  |-- Agent Service
  |     |-- Memory
  |     |-- Supervisor
  |     |-- RAG / Summary / Writing
  |     |-- agent_runs
  |
  |-- Memory Service
  |     |-- Redis short-term memory
  |     |-- user_memories long-term memory
  |
  |-- Admin Service
        |-- metrics
        |-- audit_logs

Celery Worker
  |
  |-- download from MinIO
  |-- parse document
  |-- split chunks
  |-- write document_chunks
  |-- embedding
  |-- upsert Qdrant

PostgreSQL
  |
  |-- users / knowledge_bases / documents / chunks
  |-- conversations / messages / memories
  |-- retrieval_logs / llm_call_logs / agent_runs / audit_logs

Qdrant
  |
  |-- knowledge_chunks collection
  |-- vector + payload

Redis
  |
  |-- Celery broker
  |-- short-term memory

MinIO
  |
  |-- original uploaded files
```

## 24. 读懂这个项目的最短路径

如果你只想快速掌握主线，按这个顺序理解：

1. 用户登录后拿到 token。
2. 用户创建知识库。
3. 用户上传文档。
4. 文档原文件进入 MinIO。
5. 文档记录进入 PostgreSQL。
6. Worker 后台解析文档。
7. 文档被切成 chunks。
8. chunks 写入 PostgreSQL。
9. chunks 生成 embedding 后写入 Qdrant。
10. 用户提问。
11. 系统根据权限和安全等级检索可见 chunks。
12. 多路召回后用 RRF 融合排序。
13. LLM 基于 selected chunks 生成答案。
14. 答案带 citations。
15. 系统保存检索日志、LLM 日志、Agent trace。

把这 15 步想明白，就掌握了这个项目的核心。
