# RAG Pipeline 说明

本项目的 RAG pipeline 目标是先保证“可运行、可解释、可评估”，再逐步优化效果。

## 1. 入库阶段

1. 用户上传 PDF/DOCX/TXT/MD/CSV。
2. 原文件进入 MinIO。
3. PostgreSQL 创建 `documents` 记录。
4. Celery worker 异步解析文档。
5. 文本清洗后进入 chunk splitter。
6. chunk 写入 `document_chunks`。
7. Embedding Provider 生成向量。
8. Qdrant upsert 向量点，payload 包含 `knowledge_base_id`、`document_id`、`chunk_id`、`file_name`、metadata。

## 2. 检索阶段

当前检索包含：

- Query Planning：保留原始问题，生成 rewritten query，并在复杂问题下拆出 sub-queries；去重后按 `RETRIEVAL_MAX_ROUTE_QUERIES` 截断。
- Dense Retrieval：原始、rewrite 和保留的 subquery route 都从 Qdrant 召回语义相近 chunk。
- BM25 Retrieval：同一组 route 从 PostgreSQL chunk 正文召回词项匹配候选。
- RRF 融合：按 chunk id 去重，并用加权 RRF 合并不同路线的候选排名。
- Context Compression：压缩过长 chunk，保留更相关片段。

## 3. 回答阶段

1. 选中的 chunk 组成带编号的上下文。
2. LLM Provider 基于上下文生成答案。
3. 有检索证据的回答附带 citations；无可用证据时明确说明依据不足，citations 为空。
4. `retrieval_logs` 保存 normalized query、sub-queries、routes、candidates、selected chunks 和 RRF 分数。

## 4. 评估指标

- `Recall@K`：前 K 个引用中是否命中预期来源。适合衡量“有没有找回来”。
- `MRR`：第一个正确引用越靠前，分数越高。适合衡量排序质量。
- `citation_hit_rate`：回答是否给出了命中预期来源的引用。适合衡量引用可信度。
- `answer_keyword_hit_rate`：答案是否包含预期关键词。只能作为弱信号，不能替代人工判断。

## 5. 当前边界

- 评估脚本只做轻量自动评估，不替代人工验收。
- 当前主链路不启用 metadata route 或 reranker。
- RAG Agent 复用已有 RAG service，不重写检索逻辑。
