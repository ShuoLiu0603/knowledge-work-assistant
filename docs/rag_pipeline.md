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

- Agent Query：外层 Agent 每次向 `rag(query)` 提交一条针对当前缺口的独立 query；检索层不再二次改写或拆子问题。
- Dense Retrieval：从 Qdrant 召回语义相近 chunk，并回 PostgreSQL hydration 再校验状态、范围和密级。
- BM25 Retrieval：用同一条 query 从 PostgreSQL chunk 正文与标题元数据召回词项匹配候选。
- RRF 融合：按 chunk id 去重，并用无权重 RRF 合并 Dense 与 BM25 排名。
- Context Compression：压缩过长 chunk，保留更相关片段。

## 3. 回答阶段

1. 单次工具调用把选中的 chunk 与 RetrievalLog 返回 Agent。
2. Agent 可以换一条实质不同的 query 再次调用 RAG，也可以基于累计证据回答。
3. 多批证据使用稳定编号；企业事实回答必须保留 `[1]`、`[2]` 等引用。
4. 无可用证据时明确说明依据不足，citations 为空。
5. 每条 `retrieval_logs` 保存实际 query、routes、candidates、selected chunks 和 RRF 分数；一次回答可以关联多条日志。

## 4. 评估指标

- `Recall@K`：前 K 个引用中是否命中预期来源。适合衡量“有没有找回来”。
- `MRR`：第一个正确引用越靠前，分数越高。适合衡量排序质量。
- `citation_hit_rate`：回答是否给出了命中预期来源的引用。适合衡量引用可信度。
- `answer_keyword_hit_rate`：答案是否包含预期关键词。只能作为弱信号，不能替代人工判断。

## 5. 当前边界

- 评估脚本只做轻量自动评估，不替代人工验收。
- 当前主链路不启用 metadata route 或 reranker。
- 多次检索由 Agent 总工具预算和 RAG 分预算共同限制，默认单轮最多调用 RAG 3 次。
