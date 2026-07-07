# Agentic RAG 企业知识库助手 Vibe Coding 实施计划表

> 用途：这份文档用于后续分阶段生成代码。它不是宣传稿，而是工程施工蓝图。目标是在技术覆盖全面的同时，代码保持简约、可运行、可演示、可继续扩展。

## 0. 总体取舍

### 0.1 项目目标

实现一个前后端分离的企业级 AI 知识库助手，支持：

- 用户注册、登录、JWT 鉴权
- 用户级数据隔离
- 知识库 CRUD
- 文档上传、解析、切分、Embedding、向量入库
- RAG 问答、流式输出、引用溯源
- 检索前问题改写、问题拆分、多查询扩展
- Dense + Sparse + Metadata 多路召回
- RRF 融合排序、Reranker 重排序、上下文压缩
- LangGraph 多 Agent 编排
- 短期记忆、长期记忆
- 历史会话管理
- LLM 调用日志、Token 消耗、检索命中文档、响应耗时
- Docker Compose 一键启动

### 0.2 明确不做什么

为保持程序简约，第一版不做这些：

- 不拆成多个后端微服务，只做 FastAPI 模块化单体 + Worker。
- 不上 Kubernetes，只用 Docker Compose。
- 不引入 Elasticsearch，关键词检索用 PostgreSQL Full Text Search。
- 不做复杂企业组织架构审批流，权限只做用户、知识库成员和角色。
- 不做实时协同编辑。
- 不做完整计费系统，只记录 Token 和估算成本。
- 不强依赖某个闭源模型，LLM 和 Embedding 走统一 Provider。

### 0.3 技术覆盖但代码简约的原则

| 需求 | 简约实现 | 可扩展方向 |
|---|---|---|
| 后端架构 | FastAPI 模块化单体 | 后续拆服务 |
| 异步任务 | Celery + Redis | RQ / Dramatiq / Kafka |
| 向量库 | Qdrant | PGVector / Milvus |
| 关键词检索 | PostgreSQL Full Text Search | Elasticsearch / OpenSearch |
| 对象存储 | MinIO | S3 / OSS / COS |
| Agent 编排 | LangGraph 单图 | 多图、多租户调度 |
| 前端状态 | TanStack Query + Zustand | Redux Toolkit |
| 流式输出 | SSE | WebSocket |
| 监控 | 数据库日志表 | OpenTelemetry / Prometheus |

## 1. 推荐技术栈

### 1.1 后端

| 类别 | 技术 |
|---|---|
| API 框架 | FastAPI |
| 数据校验 | Pydantic v2 |
| ORM | SQLAlchemy 2.x |
| 数据迁移 | Alembic |
| 鉴权 | JWT + Passlib bcrypt |
| 任务队列 | Celery + Redis |
| 文档解析 | pypdf, python-docx, markdown, pandas, unstructured 可选 |
| AI 编排 | LangChain, LangGraph |
| 向量库客户端 | qdrant-client |
| 对象存储 | MinIO Python SDK |
| 测试 | pytest, httpx, pytest-asyncio |

### 1.2 前端

| 类别 | 技术 |
|---|---|
| 框架 | React + TypeScript + Vite |
| 路由 | React Router |
| 请求状态 | TanStack Query |
| 本地状态 | Zustand |
| 样式 | Tailwind CSS + shadcn/ui |
| Markdown | react-markdown + rehype-highlight |
| 流式响应 | Fetch ReadableStream 或 EventSource |
| 表单 | react-hook-form + zod |

### 1.3 基础设施

| 类别 | 技术 |
|---|---|
| 数据库 | PostgreSQL |
| 缓存 | Redis |
| 向量库 | Qdrant |
| 对象存储 | MinIO |
| 容器 | Docker Compose |
| 反向代理 | Nginx 可选 |

## 2. 最终目录结构

```text
agentic-rag-platform/
  apps/
    backend/
      alembic/
      app/
        api/
          routes/
            auth.py
            knowledge_bases.py
            documents.py
            conversations.py
            memories.py
            admin.py
          deps.py
          router.py
        core/
          config.py
          security.py
          logging.py
          errors.py
        db/
          base.py
          session.py
          models/
            user.py
            knowledge_base.py
            document.py
            conversation.py
            memory.py
            log.py
          repositories/
            users.py
            knowledge_bases.py
            documents.py
            conversations.py
            memories.py
            logs.py
        schemas/
          auth.py
          knowledge_base.py
          document.py
          conversation.py
          memory.py
          log.py
        services/
          auth_service.py
          knowledge_base_service.py
          document_service.py
          chat_service.py
          memory_service.py
          log_service.py
        rag/
          loaders.py
          splitters.py
          embeddings.py
          vector_store.py
          sparse_search.py
          query_transform.py
          retrievers.py
          fusion.py
          rerankers.py
          compressor.py
          citations.py
          prompts.py
        agents/
          state.py
          graph.py
          supervisor.py
          rag_agent.py
          summary_agent.py
          writing_agent.py
          memory_agent.py
          tools.py
        providers/
          llm.py
          embedding.py
          reranker.py
        workers/
          celery_app.py
          document_tasks.py
        storage/
          minio_client.py
        main.py
      tests/
        api/
        rag/
        agents/
        services/
      Dockerfile
      pyproject.toml
      alembic.ini
    frontend/
      src/
        app/
          router.tsx
          providers.tsx
        components/
          layout/
          chat/
          knowledge-base/
          documents/
          citations/
          ui/
        pages/
          LoginPage.tsx
          RegisterPage.tsx
          KnowledgeBaseListPage.tsx
          KnowledgeBaseDetailPage.tsx
          ChatPage.tsx
          ConversationHistoryPage.tsx
          MemoriesPage.tsx
          AdminMetricsPage.tsx
        lib/
          api.ts
          auth.ts
          stream.ts
          types.ts
        stores/
          authStore.ts
          chatStore.ts
        styles/
          globals.css
      Dockerfile
      package.json
      vite.config.ts
  infra/
    docker-compose.yml
    nginx/
      nginx.conf
    postgres/
      init.sql
  docs/
    architecture.md
    api.md
    rag-pipeline.md
    prompts.md
    evaluation.md
  scripts/
    seed_demo_data.py
    run_eval.py
  .env.example
  README.md
```

## 3. 环境变量设计

`.env.example`：

```bash
APP_ENV=development
APP_NAME=agentic-rag-platform
API_PREFIX=/api

BACKEND_CORS_ORIGINS=http://localhost:5173,http://localhost:3000

POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=rag_app
POSTGRES_USER=rag_user
POSTGRES_PASSWORD=rag_password
DATABASE_URL=postgresql+psycopg://rag_user:rag_password@postgres:5432/rag_app

REDIS_URL=redis://redis:6379/0

QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=knowledge_chunks

MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=documents
MINIO_SECURE=false

JWT_SECRET_KEY=change-me
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=14

LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=replace-me
LLM_MODEL=gpt-4.1-mini

EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=replace-me
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536

RERANKER_PROVIDER=none
RERANKER_MODEL=bge-reranker-base

DEFAULT_CHUNK_SIZE=800
DEFAULT_CHUNK_OVERLAP=120
MAX_RETRIEVAL_CANDIDATES=50
FINAL_CONTEXT_CHUNKS=8
```

## 4. 数据库设计

### 4.1 users

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  username VARCHAR(100) NOT NULL,
  hashed_password TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  is_admin BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

索引：

- `idx_users_email`

### 4.2 refresh_tokens

```sql
CREATE TABLE refresh_tokens (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.3 knowledge_bases

```sql
CREATE TABLE knowledge_bases (
  id UUID PRIMARY KEY,
  owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name VARCHAR(120) NOT NULL,
  description TEXT,
  visibility VARCHAR(20) NOT NULL DEFAULT 'private',
  embedding_model VARCHAR(100) NOT NULL,
  retrieval_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

约束：

- `visibility` 取值：`private`, `team`, `public`

### 4.4 knowledge_base_members

```sql
CREATE TABLE knowledge_base_members (
  id UUID PRIMARY KEY,
  knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role VARCHAR(20) NOT NULL DEFAULT 'viewer',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (knowledge_base_id, user_id)
);
```

角色：

- `owner`
- `editor`
- `viewer`

### 4.5 documents

```sql
CREATE TABLE documents (
  id UUID PRIMARY KEY,
  knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  uploader_id UUID NOT NULL REFERENCES users(id) ON DELETE SET NULL,
  file_name TEXT NOT NULL,
  file_ext VARCHAR(20) NOT NULL,
  mime_type VARCHAR(120),
  file_size BIGINT NOT NULL,
  object_key TEXT NOT NULL,
  content_hash VARCHAR(64) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'uploaded',
  error_message TEXT,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  summary TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

状态：

- `uploaded`
- `parsing`
- `chunking`
- `embedding`
- `indexed`
- `failed`

索引：

- `idx_documents_kb_id`
- `idx_documents_status`
- `idx_documents_content_hash`

### 4.6 document_chunks

```sql
CREATE TABLE document_chunks (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  content_tsv TSVECTOR,
  token_count INTEGER NOT NULL DEFAULT 0,
  title_path TEXT,
  page_number INTEGER,
  section_name TEXT,
  qdrant_point_id UUID NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

索引：

- `idx_chunks_document_id`
- `idx_chunks_kb_id`
- `idx_chunks_tsv` 使用 GIN
- `idx_chunks_qdrant_point_id`

说明：

- `content_tsv` 用于 Sparse Keyword Retrieval。
- Qdrant 存向量和 payload，PostgreSQL 存正文和结构化元数据。

### 4.7 conversations

```sql
CREATE TABLE conversations (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(200) NOT NULL DEFAULT 'New Conversation',
  selected_knowledge_base_ids UUID[] NOT NULL DEFAULT '{}',
  summary TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.8 messages

```sql
CREATE TABLE messages (
  id UUID PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role VARCHAR(20) NOT NULL,
  content TEXT NOT NULL,
  citations JSONB NOT NULL DEFAULT '[]'::jsonb,
  agent_trace JSONB NOT NULL DEFAULT '[]'::jsonb,
  token_usage JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

角色：

- `user`
- `assistant`
- `system`
- `tool`

### 4.9 user_memories

```sql
CREATE TABLE user_memories (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  memory_type VARCHAR(30) NOT NULL,
  content TEXT NOT NULL,
  content_hash VARCHAR(64) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  importance INTEGER NOT NULL DEFAULT 1,
  confidence NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
  hit_count INTEGER NOT NULL DEFAULT 1,
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  supersedes_memory_id UUID REFERENCES user_memories(id) ON DELETE SET NULL,
  embedding_model VARCHAR(100),
  embedding_point_id UUID,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

类型：

- `profile`
- `preference`
- `task_summary`
- `domain_context`
- `feedback`

状态：

- `active`：当前生效记忆
- `superseded`：被新记忆覆盖
- `archived`：用户手动归档或系统不再使用

索引：

- `idx_user_memories_user_type_status`
- `idx_user_memories_last_seen_at`
- `idx_user_memories_embedding_point_id`
- `uq_user_memory_active_hash`：`(user_id, memory_type, content_hash)` 在 `status = 'active'` 时唯一

说明：

- `content_hash` 用于精确去重。
- `hit_count` 和 `last_seen_at` 用于统计某条记忆被重复观察到的次数。
- `supersedes_memory_id` 用于记录冲突记忆的覆盖关系。
- `embedding_point_id` 用于把长期记忆同步到 Qdrant 或 PGVector，支持语义去重和语义召回。

### 4.10 agent_runs

```sql
CREATE TABLE agent_runs (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  root_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
  graph_name VARCHAR(100) NOT NULL,
  status VARCHAR(30) NOT NULL,
  input JSONB NOT NULL DEFAULT '{}'::jsonb,
  output JSONB NOT NULL DEFAULT '{}'::jsonb,
  trace JSONB NOT NULL DEFAULT '[]'::jsonb,
  latency_ms INTEGER,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.11 retrieval_logs

```sql
CREATE TABLE retrieval_logs (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  query TEXT NOT NULL,
  rewritten_query TEXT,
  sub_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
  retrieval_routes JSONB NOT NULL DEFAULT '[]'::jsonb,
  candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
  selected_chunks JSONB NOT NULL DEFAULT '[]'::jsonb,
  latency_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.12 llm_call_logs

```sql
CREATE TABLE llm_call_logs (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  agent_name VARCHAR(100),
  provider VARCHAR(100) NOT NULL,
  model_name VARCHAR(100) NOT NULL,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  estimated_cost NUMERIC(12, 6),
  latency_ms INTEGER,
  status VARCHAR(30) NOT NULL,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 4.13 feedbacks

```sql
CREATE TABLE feedbacks (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  rating INTEGER NOT NULL,
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`rating`：

- `1` 点赞
- `-1` 点踩

## 5. Qdrant Collection 设计

Collection：`knowledge_chunks`

Vector：

```json
{
  "size": 1536,
  "distance": "Cosine"
}
```

Payload：

```json
{
  "user_id": "uuid",
  "knowledge_base_id": "uuid",
  "document_id": "uuid",
  "chunk_id": "uuid",
  "chunk_index": 12,
  "file_name": "finance_policy.pdf",
  "title_path": "财务制度 / 差旅报销 / 住宿标准",
  "page_number": 8,
  "section_name": "住宿标准",
  "created_at": "2026-07-02T00:00:00Z"
}
```

必须建立 payload index：

- `user_id`
- `knowledge_base_id`
- `document_id`
- `file_name`

检索时必须带过滤条件：

```json
{
  "must": [
    { "key": "user_id", "match": { "value": "<current_user_id>" } },
    { "key": "knowledge_base_id", "match": { "any": ["kb1", "kb2"] } }
  ]
}
```

## 6. API 设计

### 6.1 Auth

| Method | Path | 说明 | 鉴权 |
|---|---|---|---|
| POST | `/api/auth/register` | 注册 | 否 |
| POST | `/api/auth/login` | 登录 | 否 |
| POST | `/api/auth/refresh` | 刷新 Token | 否 |
| POST | `/api/auth/logout` | 退出 | 是 |
| GET | `/api/me` | 当前用户 | 是 |

登录响应：

```json
{
  "access_token": "jwt",
  "refresh_token": "jwt-or-random-token",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "username": "User"
  }
}
```

### 6.2 Knowledge Bases

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/knowledge-bases` | 列表 |
| POST | `/api/knowledge-bases` | 创建 |
| GET | `/api/knowledge-bases/{kb_id}` | 详情 |
| PATCH | `/api/knowledge-bases/{kb_id}` | 更新 |
| DELETE | `/api/knowledge-bases/{kb_id}` | 删除 |

创建请求：

```json
{
  "name": "公司制度知识库",
  "description": "用于查询公司人事、财务、行政制度",
  "visibility": "private"
}
```

### 6.3 Documents

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/knowledge-bases/{kb_id}/documents` | 上传文档 |
| GET | `/api/knowledge-bases/{kb_id}/documents` | 文档列表 |
| GET | `/api/documents/{doc_id}` | 文档详情 |
| DELETE | `/api/documents/{doc_id}` | 删除文档 |
| GET | `/api/documents/{doc_id}/chunks` | 查看 Chunk |
| POST | `/api/documents/{doc_id}/reindex` | 重新解析入库 |

上传响应：

```json
{
  "document_id": "uuid",
  "status": "uploaded",
  "job_id": "celery-task-id"
}
```

### 6.4 Conversations

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/conversations` | 历史会话 |
| POST | `/api/conversations` | 创建会话 |
| GET | `/api/conversations/{conversation_id}` | 会话详情 |
| DELETE | `/api/conversations/{conversation_id}` | 删除会话 |
| POST | `/api/conversations/{conversation_id}/messages` | 非流式问答 |
| POST | `/api/conversations/{conversation_id}/stream` | 流式问答 |

流式请求：

```json
{
  "message": "销售人员出差时住宿和交通分别怎么报销？",
  "knowledge_base_ids": ["uuid"],
  "enable_decomposition": true,
  "enable_rerank": true
}
```

### 6.5 Memories

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/memories` | 当前用户长期记忆 |
| POST | `/api/memories` | 手动添加记忆 |
| PATCH | `/api/memories/{memory_id}` | 修改记忆 |
| DELETE | `/api/memories/{memory_id}` | 删除记忆 |

### 6.6 Admin / Metrics

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/admin/llm-logs` | LLM 调用日志 |
| GET | `/api/admin/retrieval-logs` | 检索日志 |
| GET | `/api/admin/metrics` | 聚合指标 |

## 7. 文档处理链路

### 7.1 上传阶段

流程：

1. 校验用户是否有知识库编辑权限。
2. 校验文件类型和大小。
3. 计算文件 SHA256，避免重复上传。
4. 上传原文件到 MinIO。
5. PostgreSQL 创建 `documents` 记录，状态为 `uploaded`。
6. 投递 Celery 任务 `process_document(document_id)`。

限制：

- 单文件默认最大 50 MB。
- 第一版支持：PDF、DOCX、TXT、MD、CSV。
- OCR 作为增强项，不阻塞主线。

### 7.2 解析阶段

解析器接口：

```python
class ParsedBlock(TypedDict):
    text: str
    page_number: int | None
    title_path: str | None
    block_type: Literal["paragraph", "heading", "table", "list"]
    metadata: dict
```

不同文件解析策略：

| 类型 | 策略 |
|---|---|
| PDF | pypdf 按页解析，保留页码 |
| DOCX | python-docx 按段落和标题解析 |
| MD | 按标题层级解析 |
| TXT | 按段落解析 |
| CSV | 每 N 行组成一个块，列名注入上下文 |

### 7.3 清洗阶段

文本清洗规则：

- 删除空白行
- 合并连续空格
- 保留标题层级
- 保留页码
- 表格转 Markdown table 或自然语言摘要
- 丢弃长度过短且无信息量的块

不要做过度清洗：

- 不要删除编号，因为制度文档常靠编号定位。
- 不要删除金额、日期、百分比。
- 不要删除标题路径。

### 7.4 切分阶段

默认策略：

- 基于 RecursiveCharacterTextSplitter 思路实现。
- 目标大小：800 tokens 左右。
- overlap：120 tokens 左右。
- 优先按标题、段落、句号、换行切。

Chunk 结构：

```python
class Chunk(TypedDict):
    content: str
    chunk_index: int
    token_count: int
    title_path: str | None
    page_number: int | None
    section_name: str | None
    metadata: dict
```

增强策略：

- Parent-Child Chunking：第一版可以只保存 parent metadata，不必复杂实现。
- Document Summary Chunk：文档解析完成后生成一条摘要 Chunk，适合高层问题。
- Table Chunk：表格单独切，不和正文混在一起。

### 7.5 Embedding 和入库

流程：

1. 批量生成 Embedding，每批 32 或 64 个 Chunk。
2. PostgreSQL 写入 `document_chunks`。
3. Qdrant upsert points。
4. 更新文档状态为 `indexed`。
5. 出错时状态为 `failed`，保存错误信息。

失败处理：

- 解析失败：不写 Chunk。
- 部分 Embedding 失败：整体标记失败，避免文档半入库导致引用混乱。
- 重新入库：先删除旧 Chunk 和 Qdrant points，再重新处理。

## 8. RAG 检索链路详细设计

### 8.1 总流程

```mermaid
flowchart TD
    Q["用户问题"] --> M["加载短期记忆和长期记忆"]
    M --> I["意图识别"]
    I --> R["问题改写"]
    R --> D["复杂问题拆分"]
    D --> E["多查询扩展"]
    E --> V["Dense Vector Retrieval"]
    E --> S["Sparse Keyword Retrieval"]
    E --> F["Metadata Filter"]
    V --> C["候选结果合并"]
    S --> C
    F --> C
    C --> RRF["RRF 融合排序"]
    RRF --> RR["Reranker 重排序"]
    RR --> CC["上下文压缩"]
    CC --> A["基于引用生成答案"]
    A --> L["记录日志和更新记忆"]
```

### 8.2 意图识别

输出枚举：

- `knowledge_qa`
- `summary`
- `writing`
- `comparison`
- `memory_update`
- `general_chat`

第一版实现：

- 使用轻量 LLM 分类。
- 失败时默认 `knowledge_qa`。

输出格式：

```json
{
  "intent": "knowledge_qa",
  "need_retrieval": true,
  "need_decomposition": true,
  "need_memory": true,
  "reason": "用户询问制度细节，需要查询知识库"
}
```

### 8.3 问题改写 Query Rewrite

目的：

- 将依赖上下文的追问改写成独立问题。
- 补全指代词，如“这个”“刚才那个”“它”。
- 去掉口语噪声。

输入：

- 当前用户问题
- 最近 6-10 条会话消息
- 用户长期记忆摘要

输出：

```json
{
  "rewritten_query": "根据公司差旅报销制度，销售人员拜访客户时住宿、交通和招待费用分别有哪些报销限制？"
}
```

简单降级策略：

- 如果 LLM 改写失败，直接使用原问题。

### 8.4 问题拆分 Question Decomposition

触发条件：

- 问题中包含多个对象：住宿、交通、招待。
- 问题中包含比较：A 和 B 有什么区别。
- 问题需要多跳推理：先判断适用规则，再计算额度。

输出：

```json
{
  "sub_questions": [
    "销售人员出差住宿费用报销标准是什么？",
    "销售人员出差交通费用报销标准是什么？",
    "销售人员拜访客户时招待费用报销限制是什么？",
    "这些费用是否需要额外审批？"
  ]
}
```

约束：

- 子问题最多 5 个。
- 子问题必须可以独立检索。
- 不要凭空添加用户没问的业务对象。

### 8.5 多查询扩展 Multi-Query Expansion

生成查询集合：

- 原始问题
- 改写问题
- 子问题
- 关键词压缩查询
- 同义表达查询

示例：

```json
[
  "销售人员出差住宿交通招待费用报销限制",
  "销售拜访客户 差旅 住宿 交通 招待 审批 标准",
  "客户招待费用报销额度和审批要求"
]
```

第一版限制：

- 总查询数最多 8 个。
- 查询过多会拖慢速度，因此每路检索 TopK 要小。

### 8.6 Dense Vector Retrieval

输入：

- query embedding
- knowledge_base_ids
- user_id

参数：

- 每个 query 召回 Top 10。
- 总候选最多 50。
- 必须带 user_id 和 knowledge_base_id filter。

输出候选：

```python
class RetrievalCandidate(TypedDict):
    chunk_id: str
    document_id: str
    content: str
    route: str
    query: str
    score: float
    metadata: dict
```

### 8.7 Sparse Keyword Retrieval

实现：

- PostgreSQL `to_tsvector` + `plainto_tsquery`。
- 中文可先用简单文本匹配作为第一版，后续接 `zhparser` 或外部 BM25。
- 英文和数字规则文档可直接用 PostgreSQL FTS。

SQL 思路：

```sql
SELECT id, document_id, content, ts_rank(content_tsv, plainto_tsquery(:query)) AS score
FROM document_chunks
WHERE knowledge_base_id = ANY(:kb_ids)
  AND content_tsv @@ plainto_tsquery(:query)
ORDER BY score DESC
LIMIT :top_k;
```

中文降级：

```sql
SELECT id, document_id, content
FROM document_chunks
WHERE knowledge_base_id = ANY(:kb_ids)
  AND content ILIKE :keyword
LIMIT :top_k;
```

### 8.8 Metadata Retrieval

用途：

- 用户指定某个文档、日期、部门、标签时做过滤。
- 问题中出现“人事制度”“财务制度”“2025 版”等关键词时增强过滤。

第一版实现：

- 从 query 中提取文件名、标题关键词。
- 对 `documents.file_name`、`document_chunks.title_path` 做模糊匹配。

### 8.9 RRF 融合排序

原因：

- Dense 擅长语义。
- Sparse 擅长关键词和编号。
- Metadata 擅长指定范围。
- RRF 简单稳定，适合第一版。

公式：

```text
score = sum(1 / (k + rank_i))
```

默认：

- `k = 60`

伪代码：

```python
def reciprocal_rank_fusion(result_lists, k=60):
    scores = defaultdict(float)
    by_chunk = {}
    for results in result_lists:
        for rank, item in enumerate(results, start=1):
            scores[item.chunk_id] += 1 / (k + rank)
            by_chunk[item.chunk_id] = item
    return sorted(by_chunk.values(), key=lambda x: scores[x.chunk_id], reverse=True)
```

### 8.10 Reranker 重排序

优先级：

1. 如果配置了外部 Reranker，使用 Cross Encoder Rerank。
2. 如果没有 Reranker，使用 RRF 分数。

输入：

- rewritten query
- Top 50 candidate chunks

输出：

- Top 8 final chunks

Rerank 结果字段：

```json
{
  "chunk_id": "uuid",
  "rrf_score": 0.048,
  "rerank_score": 0.87,
  "selected": true
}
```

多样性约束：

- 同一文档最多选 5 个 Chunk。
- 相邻 Chunk 可以合并，但引用仍保留原 Chunk。
- 如果 Top 8 全来自同一页，保留最高 3 个，其余补充其他来源。

### 8.11 上下文压缩

策略：

- 删除低分候选。
- 合并同一文档相邻 Chunk。
- 每个 Chunk 前注入来源信息。
- 总上下文控制在模型窗口的 40%-60%。

上下文格式：

```text
[Source 1]
document_id: ...
file_name: finance_policy.pdf
page: 8
title_path: 财务制度 / 差旅报销 / 住宿标准
content:
...

[Source 2]
...
```

### 8.12 引用溯源

回答格式要求：

- 事实性结论后标注引用编号，如 `[1]`。
- 没有检索证据时必须说“不确定”。
- 不允许编造文档名、页码和制度条款。

Citation 对象：

```json
{
  "citation_id": 1,
  "document_id": "uuid",
  "document_name": "finance_policy.pdf",
  "chunk_id": "uuid",
  "page_number": 8,
  "title_path": "财务制度 / 差旅报销 / 住宿标准",
  "snippet": "住宿费标准为...",
  "rerank_score": 0.87
}
```

## 9. LangGraph 多 Agent 设计

### 9.1 AgentState

```python
class AgentState(TypedDict, total=False):
    user_id: str
    conversation_id: str
    knowledge_base_ids: list[str]
    user_query: str
    intent: str
    rewritten_query: str
    sub_questions: list[str]
    expanded_queries: list[str]
    short_term_memory: list[dict]
    long_term_memory: list[dict]
    retrieved_candidates: list[dict]
    reranked_chunks: list[dict]
    compressed_context: str
    citations: list[dict]
    draft_answer: str
    final_answer: str
    agent_trace: list[dict]
    token_usage: dict
    error: str | None
```

### 9.2 节点列表

| 节点 | 职责 |
|---|---|
| `load_memory` | 加载短期和长期记忆 |
| `supervisor` | 判断意图和路由 |
| `query_rewrite` | 问题改写 |
| `question_decompose` | 问题拆分 |
| `retrieve` | 多路召回 |
| `rerank` | 重排序 |
| `compress_context` | 上下文压缩 |
| `rag_generate` | 基于引用生成回答 |
| `summary_agent` | 总结文档或会话 |
| `writing_agent` | 生成邮件、报告、方案 |
| `memory_agent` | 判断和更新长期记忆 |
| `finalize` | 保存消息、日志、输出 |

### 9.3 Supervisor 路由逻辑

路由表：

| intent | 下一步 |
|---|---|
| `knowledge_qa` | `query_rewrite` |
| `comparison` | `query_rewrite` |
| `summary` | `summary_agent` |
| `writing` | `writing_agent` |
| `memory_update` | `memory_agent` |
| `general_chat` | `finalize` |

复杂写作任务：

- 用户说“根据知识库写一封通知邮件”：`writing_agent` 需要先调用 `rag_agent` 获取事实，再写作。

### 9.4 RAG Agent 内部步骤

```text
RAG Agent =
  query_rewrite
  -> question_decompose
  -> multi_query_expand
  -> hybrid_retrieve
  -> rrf_fusion
  -> rerank
  -> context_compress
  -> answer_with_citations
```

### 9.5 Summary Agent

能力：

- 总结当前文档
- 总结当前会话
- 总结检索结果
- 生成任务摘要，交给 Memory Agent

输出结构：

```json
{
  "summary": "...",
  "key_points": ["...", "..."],
  "open_questions": ["..."],
  "source_ids": [1, 2]
}
```

### 9.6 Writing Agent

支持类型：

- 通知
- 邮件
- 周报
- 会议纪要
- 制度说明
- 技术方案

写作原则：

- 涉及事实时必须先检索。
- 输出格式尽量结构化。
- 不编造制度、金额和日期。

### 9.7 Memory Agent

读记忆：

- 在回答前读取用户偏好和历史任务摘要。

写记忆：

- 只保存稳定、有复用价值的信息。
- 不保存一次性的临时问题。
- 用户明确要求“记住”时优先保存。
- 写入前必须做精确去重、语义去重和冲突判断。
- 对重复记忆优先更新 `hit_count` 和 `last_seen_at`，不要直接新增。
- 对补充型记忆优先合并，不要让长期记忆碎片化。
- 对冲突型记忆使用新记忆覆盖旧记忆，旧记忆标记为 `superseded`。

写入判断输出：

```json
{
  "should_write": true,
  "memory_action": "merge",
  "memory_type": "preference",
  "content": "用户偏好输出简洁、带表格的技术方案。",
  "importance": 3,
  "confidence": 0.92,
  "target_memory_id": "uuid-or-null",
  "reason": "新信息补充了用户已有的简洁输出偏好"
}
```

`memory_action` 取值：

- `ignore`：不写入。
- `create`：新增长期记忆。
- `touch`：精确重复，只更新命中次数和最近观察时间。
- `merge`：语义相近或内容互补，合并到已有记忆。
- `supersede`：新记忆与旧记忆冲突，用新记忆覆盖旧记忆。

## 10. Prompt 设计

### 10.1 Supervisor Prompt 要点

```text
你是企业知识库助手的 Supervisor Agent。
你需要判断用户意图，并决定下一步调用哪个 Agent。
只能输出 JSON，不要输出解释性自然语言。

可选 intent：
- knowledge_qa
- comparison
- summary
- writing
- memory_update
- general_chat

判断规则：
- 用户询问制度、流程、文档事实，选择 knowledge_qa。
- 用户要求比较多个对象，选择 comparison。
- 用户要求总结文档或会话，选择 summary。
- 用户要求写邮件、报告、方案，选择 writing。
- 用户要求记住、忘记、更新偏好，选择 memory_update。
- 与知识库无关的普通闲聊，选择 general_chat。
```

### 10.2 Query Rewrite Prompt 要点

```text
请把用户当前问题改写成一个可以独立检索的完整问题。
你可以使用最近对话上下文补全指代，但不能添加用户没有表达的业务对象。
只输出 JSON。

输出格式：
{
  "rewritten_query": "..."
}
```

### 10.3 Question Decomposition Prompt 要点

```text
请判断问题是否需要拆分。
如果问题包含多个条件、多个对象、比较、多跳推理，则拆成最多 5 个子问题。
每个子问题必须能独立用于知识库检索。
只输出 JSON。

输出格式：
{
  "need_decomposition": true,
  "sub_questions": ["...", "..."]
}
```

### 10.4 Answer Prompt 要点

```text
你是企业知识库问答助手。
必须严格基于给定 Source 回答。
如果 Source 中没有依据，请说无法从当前知识库确认。
回答中的关键事实后必须使用引用编号，如 [1]。
不要编造制度条款、金额、日期、页码或文档名称。
```

## 11. 记忆系统设计

### 11.1 短期记忆

存储：

- Redis 保存最近 N 轮消息。
- PostgreSQL 保存完整消息。

Redis Key：

```text
conversation:{conversation_id}:recent_messages
```

Value：

```json
[
  {"role": "user", "content": "...", "created_at": "..."},
  {"role": "assistant", "content": "...", "created_at": "..."}
]
```

策略：

- 默认保留最近 10 条消息。
- 超出后只保留摘要和最近消息。

### 11.2 长期记忆

存储：

- PostgreSQL `user_memories`
- 可选后续把长期记忆也向量化

读取策略：

- 按 `memory_type` 和 `importance` 取 Top N。
- 与当前问题关键词相关的记忆优先。

写入策略：

| 场景 | 是否写入 |
|---|---|
| 用户说“以后都用表格回答” | 是 |
| 用户说“记住我是财务部” | 是 |
| 一次性问“今天吃什么” | 否 |
| 完成一个复杂报告任务 | 写入任务摘要 |
| 用户点踩回答 | 写入反馈类型记忆或反馈表 |

### 11.3 长期记忆去重、合并与覆盖

长期记忆不能简单追加，否则会出现重复、冲突和噪声污染。第一版必须实现可控写入流程。

#### 11.3.1 写入总流程

```text
候选记忆
-> 内容标准化
-> 生成 content_hash
-> 精确去重
-> 语义相似记忆召回
-> Memory Agent 判断 create / touch / merge / supersede / ignore
-> 落库并记录 metadata
```

#### 11.3.2 内容标准化

标准化规则：

- 去掉首尾空格。
- 合并连续空白字符。
- 统一全角和半角标点。
- 移除无意义语气词。
- 对英文统一小写。
- 保留金额、日期、部门、产品名、专有名词。

生成：

```text
content_hash = sha256(user_id + memory_type + normalized_content)
```

#### 11.3.3 精确去重

查询条件：

```sql
SELECT *
FROM user_memories
WHERE user_id = :user_id
  AND memory_type = :memory_type
  AND content_hash = :content_hash
  AND status = 'active'
LIMIT 1;
```

如果命中：

- 不新增记录。
- `hit_count = hit_count + 1`
- `last_seen_at = now()`
- `importance = greatest(importance, :new_importance)`
- `metadata` 追加最近来源信息。

对应动作：

```json
{
  "memory_action": "touch",
  "target_memory_id": "existing-memory-id"
}
```

#### 11.3.4 语义去重

适用场景：

- “我喜欢回答简洁一点”
- “以后别写太啰嗦”
- “回答短一点，直接给结论”

这些不是字面重复，但语义上应该合并。

实现方式：

1. 为长期记忆生成 Embedding。
2. 写入前在当前用户、同一 `memory_type`、`status = active` 的记忆中召回 Top 5。
3. 根据相似度决定动作。

推荐阈值：

| 相似度 | 处理 |
|---|---|
| `>= 0.90` | 认为语义重复，执行 `touch` 或 `merge` |
| `0.78 - 0.90` | 交给 Memory Agent 判断 `merge` / `create` / `supersede` |
| `< 0.78` | 默认 `create` |

如果第一版不想给长期记忆单独建向量集合，可以先用同类型记忆的关键词召回 + LLM 判断作为降级方案，但表结构保留 `embedding_point_id`。

#### 11.3.5 合并策略

适用：

- 新记忆和旧记忆不冲突。
- 新记忆是旧记忆的补充。
- 合并后能减少碎片化。

示例：

旧记忆：

```text
用户偏好回答简洁。
```

新记忆：

```text
用户希望技术方案用表格展示优缺点。
```

合并后：

```text
用户偏好简洁直接的回答风格；技术方案类内容优先使用表格展示优缺点。
```

落库规则：

- 更新原 `user_memories.content`。
- 重新计算 `content_hash`。
- `hit_count = hit_count + 1`。
- `last_seen_at = now()`。
- `metadata.merge_history` 追加旧内容、新内容、来源会话和合并原因。

#### 11.3.6 冲突覆盖策略

适用：

- 新记忆和旧记忆不能同时为真。
- 用户明确修改偏好。

示例：

旧记忆：

```text
用户喜欢详细解释每一步。
```

新记忆：

```text
用户以后希望回答尽量简短，只保留关键结论。
```

处理：

1. 将旧记忆 `status` 更新为 `superseded`。
2. 新建 active 记忆。
3. 新记忆的 `supersedes_memory_id` 指向旧记忆。
4. `metadata.supersede_reason` 记录覆盖原因。

#### 11.3.7 忽略策略

以下内容不写入长期记忆：

- 一次性临时问题。
- 无复用价值的闲聊。
- 模型推断但用户没有确认的信息。
- 敏感信息，除非用户明确要求保存且产品策略允许。
- 与已有 active 记忆重复但没有新增信息的内容。

#### 11.3.8 长期记忆向量集合

可选 Qdrant Collection：`user_memories`

Payload：

```json
{
  "user_id": "uuid",
  "memory_id": "uuid",
  "memory_type": "preference",
  "status": "active",
  "importance": 3,
  "last_seen_at": "2026-07-02T00:00:00Z"
}
```

检索过滤：

- 必须过滤 `user_id`
- 必须过滤 `status = active`
- 优先过滤同一 `memory_type`

### 11.4 自动总结与记忆沉淀

触发时机：

- 当前会话消息超过 10 条。
- 单次复杂任务完成后。
- 用户明确要求“记住这个结论”。
- 用户反馈点踩并填写原因。
- 对话即将超过模型上下文窗口。

流程：

```text
Summary Agent 生成会话或任务摘要
-> Memory Agent 提取候选长期记忆
-> 执行长期记忆去重流程
-> 写入 user_memories
-> 更新 conversations.summary
```

沉淀内容：

- 用户稳定偏好
- 用户身份和业务背景
- 历史复杂任务摘要
- 用户明确要求记住的信息
- 高频反馈中体现出的偏好

## 12. 流式响应设计

### 12.1 SSE 事件类型

使用 `POST /stream` 返回 `text/event-stream`。

事件：

| event | data |
|---|---|
| `trace` | 当前 Agent 节点，例如 `query_rewrite` |
| `retrieval` | 检索命中文档摘要 |
| `citation` | 引用来源 |
| `token` | LLM 输出 token |
| `done` | 完成 |
| `error` | 错误 |

示例：

```text
event: trace
data: {"node":"query_rewrite","status":"started"}

event: token
data: {"text":"根据"}

event: citation
data: {"citation_id":1,"document_name":"finance_policy.pdf","page_number":8}

event: done
data: {"message_id":"uuid"}
```

### 12.2 前端处理

前端需要：

- 逐 token 追加 assistant 消息。
- 收到 citation 后更新右侧引用面板。
- 收到 trace 后显示“正在检索 / 正在重排序 / 正在生成”。
- 出错时保留用户消息，展示重试按钮。

## 13. 前端页面详细设计

### 13.1 登录页

功能：

- 邮箱、密码登录
- 保存 access token
- 登录后跳转知识库列表

### 13.2 知识库列表页

展示：

- 知识库名称
- 描述
- 文档数量
- 最近更新时间

操作：

- 创建知识库
- 进入详情
- 删除知识库

### 13.3 知识库详情页

区域：

- 基本信息
- 文档列表
- 上传入口
- 文档状态

文档列表字段：

- 文件名
- 大小
- 状态
- Chunk 数
- 上传时间
- 错误信息

### 13.4 对话页

布局：

- 左侧：历史会话
- 中间：聊天窗口
- 顶部：知识库选择器
- 右侧：引用来源和检索过程

交互：

- 流式输出
- 停止生成
- 重新生成
- 复制回答
- 点赞 / 点踩
- 查看引用原文
- 展开“检索细节”，查看问题拆分、多路召回、Rerank 分数

### 13.5 记忆管理页

功能：

- 查看长期记忆
- 手动添加记忆
- 编辑记忆
- 删除记忆

### 13.6 指标页

展示：

- 请求数
- 平均响应耗时
- Token 消耗
- 检索命中率
- 点赞率
- 最近 LLM 调用日志

## 14. 后端模块职责

### 14.1 services

服务层只处理业务流程，不直接写复杂 SQL。

| 文件 | 职责 |
|---|---|
| `auth_service.py` | 注册、登录、Token |
| `knowledge_base_service.py` | 知识库权限和 CRUD |
| `document_service.py` | 上传、状态、重建索引 |
| `chat_service.py` | 会话、消息、流式输出 |
| `memory_service.py` | 记忆读写 |
| `log_service.py` | LLM 和检索日志 |

### 14.2 rag

| 文件 | 职责 |
|---|---|
| `loaders.py` | 文档解析 |
| `splitters.py` | 文本切分 |
| `embeddings.py` | Embedding 调用 |
| `vector_store.py` | Qdrant 操作 |
| `sparse_search.py` | PostgreSQL FTS |
| `query_transform.py` | 改写、拆分、扩展 |
| `retrievers.py` | 多路召回 |
| `fusion.py` | RRF |
| `rerankers.py` | Rerank |
| `compressor.py` | 上下文压缩 |
| `citations.py` | 引用结构 |
| `prompts.py` | Prompt 模板 |

### 14.3 agents

| 文件 | 职责 |
|---|---|
| `state.py` | AgentState 类型 |
| `graph.py` | LangGraph 组装 |
| `supervisor.py` | 意图识别和路由 |
| `rag_agent.py` | RAG 节点 |
| `summary_agent.py` | 总结 |
| `writing_agent.py` | 写作 |
| `memory_agent.py` | 记忆判断 |
| `tools.py` | Agent 可调用工具 |

## 15. 生成代码计划表

> 每一步都应该独立可运行、可验收。不要一次生成整个大项目。

| 阶段 | 目标 | 生成内容 | 验收方式 |
|---|---|---|---|
| 0 | 初始化仓库 | 根目录、README、`.env.example`、基础目录 | 目录结构正确 |
| 1 | Docker Compose | PostgreSQL、Redis、Qdrant、MinIO、backend、frontend、worker | `docker compose up` 服务启动 |
| 2 | FastAPI 骨架 | `main.py`、配置、健康检查、CORS、错误处理 | `GET /api/health` 正常 |
| 3 | 数据库基础 | SQLAlchemy、Alembic、Session、模型基类 | 能执行 migration |
| 4 | 用户鉴权 | users、refresh_tokens、注册、登录、JWT | 登录后访问 `/api/me` |
| 5 | 前端鉴权 | 登录页、注册页、token 存储、路由守卫 | 登录后进入知识库页 |
| 6 | 知识库 CRUD | 后端接口、权限校验、前端列表和创建弹窗 | 能创建和删除知识库 |
| 7 | 文档上传 | MinIO 客户端、上传接口、文档表、前端上传 | 上传后 documents 有记录 |
| 8 | Worker 骨架 | Celery、Redis broker、任务状态更新 | 上传后触发 worker |
| 9 | 文档解析和切分 | loaders、splitters、chunk 入库 | 文档状态变 indexed，chunk_count > 0 |
| 10 | Embedding + Qdrant | embedding provider、qdrant collection、upsert | Qdrant 有 point |
| 11 | 基础 RAG | dense retrieval、answer prompt、非流式问答 | 提问能返回答案和引用 |
| 12 | 流式问答 | SSE、前端流式渲染 | 前端逐字输出 |
| 13 | 会话历史 | conversations、messages、左侧历史会话 | 刷新后历史还在 |
| 14 | 问题改写和拆分 | query_transform、日志记录 | 检索日志有 rewritten_query 和 sub_questions |
| 15 | 多路召回 | dense、sparse、metadata retrieval | 日志显示 retrieval_routes |
| 16 | RRF + Rerank | fusion、reranker provider、final chunks | 日志显示 rrf_score/rerank_score |
| 17 | LangGraph Agent | AgentState、Supervisor、RAG Agent | agent_runs 有 trace |
| 18 | Summary/Writing Agent | 总结和写作节点 | 可“总结并写通知” |
| 19 | 记忆系统 | Redis 短期记忆、PostgreSQL 长期记忆、自动摘要、记忆去重合并 | 多轮追问可理解上下文；重复偏好不会新增多条 |
| 20 | 可观测性 | llm_call_logs、retrieval_logs、metrics API | 指标页能展示日志 |
| 21 | 评估脚本 | QA 数据集、Recall@K、引用准确率 | `python scripts/run_eval.py` 输出指标 |
| 22 | 简历包装 | README、架构图、演示数据、截图 | 项目可展示 |

## 16. 每阶段 Vibe Coding 指令模板

### 阶段 0 指令

```text
请按 VIBE_CODING_IMPLEMENTATION_PLAN.md 初始化项目目录。
只生成根目录、apps/backend、apps/frontend、infra、docs、scripts、README.md、.env.example。
不要实现业务代码。
验收标准：目录结构与计划一致。
```

### 阶段 1 指令

```text
请实现 Docker Compose 基础设施。
服务包括 postgres、redis、qdrant、minio、backend、worker、frontend。
backend 和 frontend 可以先使用占位 Dockerfile。
不要实现业务代码。
验收标准：docker compose up 后 postgres、redis、qdrant、minio 健康。
```

### 阶段 2 指令

```text
请实现 FastAPI 后端最小骨架。
包括配置读取、CORS、统一错误处理、/api/health。
保持代码简洁，不要提前实现业务模块。
验收标准：GET /api/health 返回 {"status":"ok"}。
```

### 阶段 4 指令

```text
请实现用户注册、登录、JWT 鉴权。
包括 users、refresh_tokens 模型，密码哈希，access token，refresh token，/api/me。
只做必要的字段和校验。
验收标准：用户可注册、登录，并携带 Bearer token 访问 /api/me。
```

### 阶段 9 指令

```text
请实现文档解析和切分。
支持 PDF、DOCX、TXT、MD、CSV。
解析时保留 page_number、title_path、section_name。
切分结果写入 document_chunks。
不要引入 OCR。
验收标准：上传文档后状态变 indexed，chunk_count 大于 0。
```

### 阶段 15 指令

```text
请实现高级 RAG 多路召回。
包括 query rewrite、question decomposition、multi-query expansion、dense retrieval、PostgreSQL sparse retrieval、metadata retrieval、RRF 融合。
要记录 retrieval_logs。
不要改变已有基础 RAG API 的调用方式。
验收标准：一次复杂提问能在日志中看到 rewritten_query、sub_questions、retrieval_routes、candidates。
```

### 阶段 17 指令

```text
请基于 LangGraph 实现多 Agent 编排。
包括 AgentState、Supervisor Agent、RAG Agent、Summary Agent、Writing Agent、Memory Agent 的最小可运行版本。
Supervisor 根据 intent 路由。
先复用已有 RAG service，不重复写检索逻辑。
验收标准：agent_runs 表能保存每个节点的执行 trace。
```

### 阶段 19 指令

```text
请实现记忆系统。
包括 Redis 短期记忆、conversations.summary 会话摘要、PostgreSQL 长期记忆、Memory Agent 自动记忆沉淀。
长期记忆写入前必须执行 content_hash 精确去重、语义相似记忆召回、Memory Agent 决策。
支持 create、touch、merge、supersede、ignore 五种动作。
重复偏好只更新 hit_count 和 last_seen_at；互补偏好合并；冲突偏好把旧记忆标记为 superseded。
不要把所有用户消息都写入长期记忆。
验收标准：用户多次表达相似偏好时 user_memories 不新增重复 active 记录；用户修改偏好时旧记录变 superseded。
```

## 17. 测试计划

### 17.1 后端测试

| 模块 | 测试重点 |
|---|---|
| auth | 注册、登录、密码错误、Token 过期 |
| knowledge base | 用户只能访问自己的知识库 |
| document | 上传、状态变化、重复文件 |
| splitter | 长文本切分、overlap、metadata 保留 |
| retrieval | filter 生效、dense/sparse 合并、RRF 排序 |
| reranker | 无 reranker 时降级 |
| chat | 消息保存、引用返回、日志记录 |
| memory | 短期上下文、长期记忆写入、精确去重、语义合并、冲突覆盖 |

### 17.2 前端测试

第一版不必上复杂 E2E，至少手工验收：

- 登录注册流程
- 创建知识库
- 上传文档
- 查看文档状态
- 提问并看到流式输出
- 查看引用卡片
- 查看历史会话

### 17.3 RAG 评估数据

准备 `eval/questions.jsonl`：

```json
{"question":"员工出差住宿费标准是多少？","expected_doc":"finance_policy.pdf","expected_keywords":["住宿","标准","报销"]}
{"question":"客户招待费用是否需要审批？","expected_doc":"sales_policy.pdf","expected_keywords":["招待","审批"]}
```

评估输出：

```json
{
  "total": 20,
  "recall_at_5": 0.85,
  "mrr": 0.72,
  "citation_hit_rate": 0.8,
  "avg_latency_ms": 4200
}
```

## 18. 日志与可观测性

每次问答记录：

- `request_id`
- `user_id`
- `conversation_id`
- 原始问题
- 改写问题
- 子问题
- 检索路线
- 候选 Chunk
- RRF 分数
- Rerank 分数
- 最终引用
- Prompt tokens
- Completion tokens
- Total tokens
- LLM latency
- Retrieval latency
- Total latency
- Agent trace

Agent trace 示例：

```json
[
  {"node":"load_memory","status":"success","latency_ms":18},
  {"node":"supervisor","status":"success","latency_ms":620,"intent":"knowledge_qa"},
  {"node":"query_rewrite","status":"success","latency_ms":810},
  {"node":"retrieve","status":"success","latency_ms":340,"candidate_count":42},
  {"node":"rerank","status":"success","latency_ms":900,"selected_count":8},
  {"node":"rag_generate","status":"success","latency_ms":2800}
]
```

## 19. 安全与权限

必须做：

- 密码 bcrypt 哈希。
- JWT secret 从环境变量读取。
- 所有知识库、文档、Chunk、会话查询必须带 `user_id` 或成员权限过滤。
- 上传文件限制大小和扩展名。
- 文档下载或引用查看必须校验权限。
- Qdrant 检索必须加 payload filter。
- 日志中不要保存完整 access token。

第一版可以不做：

- SSO
- LDAP
- 企业微信登录
- 数据脱敏审批
- 审计报表导出

## 20. README 展示重点

README 必须包含：

- 项目简介
- 技术栈图
- 架构图
- RAG pipeline 图
- LangGraph Agent 图
- Docker Compose 启动方式
- 环境变量说明
- 演示账号
- 演示数据
- 核心功能截图
- 简历亮点

## 21. 简历项目描述

可以直接使用：

```text
Agentic RAG 企业知识库助手
- 设计并实现前后端分离的企业级 AI 知识库平台，基于 FastAPI、React、PostgreSQL、Redis、Qdrant、MinIO 和 Docker Compose 完成用户鉴权、知识库管理、文档上传、异步解析、向量化入库、流式问答和历史会话管理。
- 基于 LangChain 与 LangGraph 构建多智能体协作流程，设计 Supervisor Agent 统一调度 RAG Agent、Summary Agent、Writing Agent 和 Memory Agent，实现复杂任务拆解、工具调用、结果整合和执行链路追踪。
- 实现检索前问题改写、问题拆分、Multi-Query Expansion、Dense/Sparse/Metadata 多路召回、RRF 融合排序、Cross Encoder Rerank、上下文压缩和引用溯源，提升企业知识库问答的召回率、准确性和可解释性。
- 设计短期记忆与长期记忆模块，使用 Redis 管理多轮对话上下文，使用 PostgreSQL 存储用户偏好、历史任务摘要和个性化信息，并通过 Summary Agent 与 Memory Agent 实现自动摘要沉淀、精确去重、语义合并和冲突覆盖。
- 建立 LLM 调用日志与 RAG 可观测体系，记录 Token 消耗、模型耗时、检索命中文档、Rerank 分数、引用片段和响应延迟，为效果评估、成本分析和 Prompt 优化提供数据基础。
```

## 22. 技术关键词

```text
FastAPI, React, TypeScript, Vite, PostgreSQL, Redis, Qdrant, MinIO,
Docker Compose, JWT, SQLAlchemy, Alembic, Celery, LangChain, LangGraph,
Agentic RAG, Supervisor Agent, Multi-Agent Workflow, Query Rewrite,
Question Decomposition, Multi-Query Expansion, Hybrid Search, Dense Retrieval,
Sparse Retrieval, PostgreSQL Full Text Search, Metadata Filter, RRF,
Reranker, Cross Encoder, Context Compression, Citation Grounding,
Streaming SSE, Short-term Memory, Long-term Memory, LLM Observability,
Memory Deduplication, Semantic Memory Merge, Token Usage Tracking, Retrieval Evaluation
```

## 23. 最小可运行路径

如果时间紧，按这个最小路径做：

1. 登录注册。
2. 知识库 CRUD。
3. 文档上传到 MinIO。
4. 文档解析切分入 PostgreSQL。
5. Embedding 入 Qdrant。
6. 基础 Dense RAG + 引用。
7. 流式聊天 + 历史会话。
8. 问题改写 + 问题拆分。
9. Sparse 检索 + RRF。
10. LangGraph Supervisor + RAG Agent。
11. 日志和 README。

这条路径已经足够简历展示。Reranker、Writing Agent、Memory Agent、评估脚本可以作为后续增强，但文档和接口要预留好。

## 24. 代码简约守则

后续写代码时遵守：

- 一个功能只放一个明确模块，不做过早抽象。
- Provider 层只封装 LLM、Embedding、Reranker 三类外部模型。
- RAG pipeline 用普通函数串起来，LangGraph 节点复用这些函数。
- 不为未来模型写复杂插件系统，只做简单配置切换。
- 不写复杂权限框架，先用函数 `ensure_kb_access(user_id, kb_id, role)`。
- 不写通用工作流引擎，LangGraph 只服务当前 Agent 流程。
- 测试优先覆盖权限、文档入库、检索过滤和引用返回。
- 每个阶段完成后能启动、能点、能演示。
