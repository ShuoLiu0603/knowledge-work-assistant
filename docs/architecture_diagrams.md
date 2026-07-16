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
  MEMORY --> QDRANT
```

说明：

- 前端只负责交互、token 携带、流式渲染和引用展示。
- FastAPI API 层负责路由、schema 和依赖注入。
- Service 层承载业务边界，例如鉴权、知识库权限、文档处理投递、对话编排。
- PostgreSQL 存关系数据、文档 chunk、会话、消息、检索日志、Agent trace 和长期记忆。
- Qdrant 存文档与长期记忆的可重建向量点；文档 payload 带知识库、文档和密级边界，记忆 payload 带用户与状态边界。
- Redis 同时服务 Celery 队列和短期记忆。
- LLM Provider 隔离 OpenAI-compatible 调用细节；模型不可用时请求明确失败，不用本地规则伪造答案。

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
  A->>MEM: load core profile + conversation context
  loop bounded model/tool steps
    A->>LLM: decide: answer, memory(query), or rag(query)
    opt memory(query)
      A->>MEM: recall ordinary long-term memory
      MEM-->>A: memory observation
    end
    opt rag(query)
      A->>RAG: authorized Dense + BM25 + RRF
      RAG-->>A: evidence + RetrievalLog
    end
  end
  LLM-->>A: final answer
  A->>LOG: save initial agent_run and trace
  A-->>API: answer, citations, run id
  API->>LOG: persist assistant message and associations
  API->>MEM: apply deferred update or enqueue durable job
  API->>LOG: update final agent_run state
  API-->>U: SSE tokens + citations
```

图中的 Memory 更新发生在 assistant Message 提交之后：开发模板默认同步执行，生产模板默认创建 durable Celery job。Qdrant 只是记忆派生索引，PostgreSQL 始终保存权威状态。
