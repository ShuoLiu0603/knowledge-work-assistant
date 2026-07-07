# 简历项目描述

## 简历版

**Agentic RAG 企业知识库助手**

基于 FastAPI、React、PostgreSQL、Redis、Qdrant、MinIO 和 Celery 构建企业知识库问答系统，支持用户鉴权、私有知识库权限隔离、文档异步解析入库、Hybrid Retrieval、引用溯源、SSE 流式对话、Agent trace、短期/长期记忆和 RAG 评估脚本。项目通过 Docker Compose 本地一键启动，并提供演示数据、验收清单和 Recall@K/MRR/citation_hit_rate 评估流程。

## 可放在项目经历中的要点

- 设计并实现企业知识库主链路：文档上传到 MinIO，Celery 异步解析 PDF/DOCX/TXT/MD/CSV，chunk 入 PostgreSQL，向量写入 Qdrant。
- 实现基于 JWT 的用户体系和 owner/editor/viewer 权限边界，确保知识库、文档、会话和检索结果按用户隔离。
- 构建可解释 RAG：保留原始问题并拆分 sub-queries，Dense 向量召回 + BM25 词项召回并行执行，按 chunk id 去重后用加权 RRF 融合，配合上下文压缩和 retrieval logs 定位召回质量。
- 实现流式问答体验：SSE 逐 token 渲染、历史会话持久化、引用来源面板、检索解释面板和 Agent trace 面板。
- 实现 Agent 编排 MVP：Supervisor、RAG、Summary、Writing、Memory Agent 复用同一 RAG service，并保存 AgentState 快照。
- 实现记忆系统：Redis 短期记忆、`conversations.summary`、`user_memories` 长期记忆、content_hash 去重、语义合并、冲突覆盖。
- 补充工程质量资产：后端关键单元测试、手工验收清单、RAG 评估脚本、演示数据和架构文档。

## 面试讲解顺序

1. 先讲主链路：上传文档 -> 解析切分 -> 向量入库 -> 检索 -> 带引用回答。
2. 再讲工程边界：鉴权、知识库权限、异步 worker、对象存储和数据库职责。
3. 然后讲 RAG 效果：为什么要 Dense + BM25 hybrid retrieval、为什么不能直接混合不同分数、如何用 RRF 融合以及如何用检索日志和评估指标排查。
4. 最后讲 Agent 与记忆：Agent 不重写 RAG，Memory 不简单追加，trace 能解释一次运行。

## 一句话亮点

不是只做一个聊天 demo，而是把企业知识库问答拆成可启动、可入库、可检索、可引用、可解释、可评估、可演示的完整工程闭环。
