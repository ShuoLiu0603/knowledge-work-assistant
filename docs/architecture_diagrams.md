# 架构图说明

## 总体架构

```mermaid
flowchart LR
  FE[React/Vite 前端] --> API[FastAPI API]
  API --> AUTH[Auth Service]
  API --> KB[Knowledge Base Service]
  API --> DOC[Document Service]
  API --> CHAT[Conversation / Agent Service]
  DOC --> MINIO[(MinIO 原文件)]
  DOC --> REDIS[(Redis / Celery Broker)]
  REDIS --> WORKER[Celery Worker]
  WORKER --> PG[(PostgreSQL)]
  WORKER --> QDRANT[(Qdrant)]
  CHAT --> RAG[RAG Pipeline]
  RAG --> PG
  RAG --> QDRANT
  CHAT --> LLM[LLM Provider]
  CHAT --> MEMORY[Memory Service]
  MEMORY --> REDIS
  MEMORY --> PG
```

说明：

- 前端只负责交互、token 携带、流式渲染和引用展示。
- FastAPI API 层负责路由、schema 和依赖注入。
- Service 层承载业务边界，例如鉴权、知识库权限、文档处理投递、对话编排。
- PostgreSQL 存关系数据、文档 chunk、会话、消息、检索日志、Agent trace 和长期记忆。
- Qdrant 存向量检索点位，payload 带知识库和文档边界。
- Redis 同时服务 Celery 队列和短期记忆。
- LLM Provider 隔离 OpenAI-compatible 调用细节；模型不可用时直接暴露错误。

## 文档入库链路

```mermaid
sequenceDiagram
  participant U as User
  participant API as FastAPI
  participant M as MinIO
  participant PG as PostgreSQL
  participant C as Celery
  participant Q as Qdrant

  U->>API: upload document
  API->>M: store original file
  API->>PG: create document(status=uploaded)
  API->>C: enqueue process_document
  C->>M: read original file
  C->>PG: status=parsing/chunking/embedding
  C->>PG: insert document_chunks
  C->>Q: upsert vectors
  C->>PG: status=indexed, chunk_count=n
```

## 问答链路

```mermaid
sequenceDiagram
  participant U as User
  participant API as Conversation API
  participant A as Agent Service
  participant MEM as Memory Service
  participant RAG as RAG Service
  participant LLM as LLM Provider
  participant LOG as Logs

  U->>API: ask question
  API->>A: run_agent
  A->>MEM: load short/long memory
  A->>LLM: classify intent
  A->>RAG: retrieve and build grounded answer
  RAG->>LLM: answer with retrieved context
  A->>MEM: update user memories
  A->>LOG: save agent_run and trace
  API-->>U: SSE tokens + citations
```
