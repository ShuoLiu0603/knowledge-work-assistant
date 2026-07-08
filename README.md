# Agentic RAG 企业知识库助手

这是一个面向企业制度文档的 Agentic RAG 项目，已经具备本地工程闭环：用户鉴权、用户检索等级、公开/私人知识库、文档密级、文档上传与异步处理、单向量库安全过滤、Hybrid Retrieval、引用溯源、SSE 流式对话、Agent trace、短期/长期记忆，以及轻量 RAG 评估脚本。

## 本地启动

```bash
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

常用地址：

- 前端：http://localhost:5173
- 管理员控制台：http://localhost:5173/admin
- 记忆管理页：http://localhost:5173/memories
- 后端健康检查：http://localhost:8000/api/health
- 后端依赖就绪检查：http://localhost:8000/api/ready
- 当前用户：http://localhost:8000/api/me
- 知识库接口：http://localhost:8000/api/knowledge-bases
- 文档接口：http://localhost:8000/api/knowledge-bases/{kb_id}/documents
- 流式消息接口：http://localhost:8000/api/conversations/{conversation_id}/messages/stream
- Agent 运行接口：http://localhost:8000/api/agent-runs
- 长期记忆接口：http://localhost:8000/api/memories
- LLM 调用日志：http://localhost:8000/api/llm-logs
- 用户反馈接口：http://localhost:8000/api/feedbacks
- 指标聚合接口：http://localhost:8000/api/admin/metrics
- 检索日志接口：http://localhost:8000/api/retrieval-logs
- Qdrant：http://localhost:6333
- MinIO 控制台：http://localhost:9001

停止服务：

```bash
docker compose -f infra/docker-compose.yml down
```

## 数据库迁移

开发环境仍会在应用启动时执行轻量 `create_all` 和运行时补列，方便本地演示。正式环境建议关闭自动建表并使用 Alembic：

```text
AUTO_CREATE_TABLES=false
```

生产环境关闭自动建表后，需要先执行迁移：

```bash
cd apps/backend
alembic upgrade head
```

迁移脚本位于 `apps/backend/alembic/versions`。

当 `APP_ENV=production` 时，后端会拒绝使用开发级配置启动，包括默认 JWT 密钥、SQLite、`AUTO_CREATE_TABLES=true` 和通配 CORS。
生产 JWT 密钥至少需要 32 字节；用户密码使用 bcrypt 哈希，旧版 PBKDF2 哈希仍可验证以兼容已有本地演示数据。

## LLM Provider

系统只支持真实 LLM 回答。Agent 路由、记忆抽取、摘要、写作和基于检索上下文的答案生成都会调用兼容 OpenAI Chat Completions 的服务：

```text
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=你的密钥
LLM_MODEL=gpt-4o-mini
```

如果 LLM 缺少配置、依赖缺失或调用失败，系统会直接报错，不再降级到本地规则回答。

Embedding 同样只支持真实向量服务，需要配置兼容 OpenAI Embeddings 的服务：

```text
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=你的密钥
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=384
```

检索链路使用原始问题和必要拆出的 sub-queries，同时走 Dense 向量召回和 BM25 词项召回；不同路线的候选通过 RRF 融合，不再启用 metadata route 或 reranker。

## 企业检索安全模型

系统使用单一 Qdrant collection 存储所有文档向量，通过 payload filter 和数据库过滤共同控制可见范围：

- 用户拥有 `security_level`，范围为 `L1` 到 `L5`。
- 第一个注册用户会自动成为 `L5` 管理员，用于本地部署 bootstrap；后续用户默认为普通 `L1`。
- 管理员可以创建和管理公开知识库；普通用户可以创建和管理自己的私人知识库。
- 公开知识库文档上传和删除仅限管理员，上传时可设置文档密级；私人知识库创建后默认仅 owner 可见，文档不需要选择密级，库内内容对有成员权限的用户全部可读。
- Dense 和 BM25 两路检索在公开知识库中都会过滤 `security_level <= current_user.security_level`；私人知识库只按成员权限隔离。
- 管理员可在 `/admin` 的“用户等级”区域调整用户等级。
- 会话支持删除；聊天流式输出使用后端 SSE 和 provider token callback，不再只做前端假打字效果。
- 关键操作写入审计日志，包括用户等级调整、文档上传、RAG 检索和会话删除；管理端可查看最近审计记录。
- 当 RAG 没有命中当前用户可访问内容时，仍会调用 LLM，并由提示词约束它说明知识库依据不足、不得用通用知识编造答案。

## Agent 编排

默认使用 LangGraph 组装 `Memory -> Supervisor -> RAG/Memory/Chat/Summary/Writing -> Memory` 单图：

```text
AGENT_GRAPH_BACKEND=langgraph
```

如果本地环境没有安装 LangGraph，`AGENT_GRAPH_BACKEND=langgraph` 会直接失败。需要显式使用顺序执行调试时，可配置：

```text
AGENT_GRAPH_BACKEND=sequential
```

## 演示路径

1. 启动服务并打开 `http://localhost:5173`。
2. 注册第一个用户；该用户会自动成为管理员。
3. 进入“知识库”页面，管理员可创建公开知识库：`企业制度演示知识库`；普通用户也可创建私人知识库。
4. 在知识库详情页上传 [company_policy_demo.md](demo/company_policy_demo.md)；公开知识库选择合适文档密级，私人知识库直接上传即可。
5. 等待文档状态变为 `indexed`，确认 `chunk_count > 0`。
6. 进入“问答”页，提问：`住宿报销上限是多少？`
7. 查看答案、引用面板、检索解释、Agent trace。
8. 输入 `I prefer concise answers`，再输入一次确认记忆触发 `touch`。
9. 输入 `I prefer detailed answers`，确认旧偏好变为 `superseded`。
10. 在回答下点击点赞/点踩，进入“管理员控制台”查看反馈率、Token、Fallback 和最近 LLM 错误。
11. 运行 RAG 评估脚本。

## 后端测试

一键质量门禁：

```bash
python scripts/check_project.py
```

该命令会运行后端单测、Python 编译检查、前端构建和 Docker Compose 配置校验。
其中也包含 Alembic 迁移验证，确保 `AUTO_CREATE_TABLES=false` 时迁移后的表结构覆盖当前模型。
如果本地服务已经启动，可以追加端到端冒烟：

```bash
python scripts/check_project.py --with-smoke
```

容器内运行：

```bash
docker exec rag-backend python -m unittest discover -s tests
```

本地运行：

```bash
$env:PYTHONPATH="apps/backend"
python -m unittest discover -s apps/backend/tests
```

测试覆盖重点：

- 密码哈希、JWT、refresh token 轮换和撤销。
- 知识库 owner/viewer 权限边界。
- 首个注册用户自动成为管理员，普通用户不能上传或删除文档。
- Memory action：`create`、`touch`、`merge`、`supersede`、`pending`、`ignore`。
- RAG 评估指标：Recall@K、MRR、citation_hit_rate。

## RAG 评估

全栈冒烟验收会自动注册临时用户、创建知识库、上传 demo 文档、等待入库并执行一次带引用问答：

```bash
python scripts/smoke_demo.py
```

先完成演示文档上传，并拿到知识库 id，然后运行：

```bash
python scripts/run_eval.py ^
  --base-url http://localhost:8000/api ^
  --email demo@example.com ^
  --password Password123! ^
  --kb-id <knowledge_base_id> ^
  --dataset demo/rag_eval_questions.json ^
  --top-k 5 ^
  --output .run/rag_eval_report.json
```

PowerShell 单行示例：

```powershell
python scripts/run_eval.py --base-url http://localhost:8000/api --email demo@example.com --password Password123! --kb-id "<knowledge_base_id>" --dataset demo/rag_eval_questions.json --top-k 5
```

输出指标：

- `Recall@K`：前 K 个引用是否命中预期来源。
- `MRR`：第一个正确引用的排序质量。
- `citation_hit_rate`：引用是否命中预期来源。
- `answer_keyword_hit_rate`：答案是否包含预期关键词，只作为弱信号。

## 文档索引

- [完整技术 Pipeline 文档](docs/technical_pipeline_guide.md)
- [手工验收清单](docs/manual_acceptance_checklist.md)
- [API 参考](docs/api.md)
- [架构图说明](docs/architecture_diagrams.md)
- [RAG Pipeline 说明](docs/rag_pipeline.md)
- [Prompt 说明](docs/prompts.md)
- [RAG 评估说明](docs/evaluation.md)
- [Agent 编排说明](docs/agent_orchestration.md)
- [简历项目描述](docs/resume_project.md)
- [面试备战指南](docs/interview_prep_guide.md)
- [演示数据说明](demo/README.md)
- [早期架构校准](docs/architecture.md)

## 阶段 11 完成标准

- 后端关键测试可以运行并通过。
- 手工验收清单覆盖启动、鉴权、入库、问答、Agent、记忆和评估。
- RAG 评估脚本能对 demo 问题输出 Recall@K、MRR 和 citation_hit_rate。
- README 能指导新用户从启动到演示。
- 架构、RAG、Agent 和简历文档能支撑项目展示与面试讲解。
- 没有新增业务功能，没有重写项目结构。
