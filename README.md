# Knowledge Work Assistant / Agentic RAG 企业知识工作助手

[![CI](https://github.com/ShuoLiu0603/knowledge-work-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/ShuoLiu0603/knowledge-work-assistant/actions/workflows/ci.yml)

[English](README_EN.md) | 简体中文

Knowledge Work Assistant 是一个面向企业知识场景的 Agentic RAG 工程化参考实现。项目将身份与权限、文档异步入库、混合检索、受控 Agent 编排、对话记忆、可追踪引用、后台任务恢复和治理审计整合为一套可运行的全栈系统。

**项目状态：工程参考实现，不是开箱即用的生产成品。** 当前代码适合架构研究、原型验证、内部技术评估和二次开发；真实业务上线前仍需完成安全、运维、负载和灾难恢复验证。

> [!WARNING]
> 不要将开发 Compose 或示例凭据直接暴露到公网。开发模板包含本地默认密码，首个注册用户会成为引导管理员，前端当前将访问令牌与刷新令牌保存在 `localStorage`。生产部署必须替换全部密钥、限制网络入口、启用 TLS，并完成身份、令牌、监控、备份和灾难恢复设计。

## 产品能力

| 领域 | 当前实现 |
|---|---|
| 身份与权限 | 注册、登录、访问/刷新令牌、管理员角色、部门范围、知识库成员角色、L1-L5 文档密级 |
| 企业知识库 | 公开/部门/私有知识库、owner/editor/viewer 访问模型、PDF/DOCX/TXT/Markdown/CSV 上传、文档状态与分块查看 |
| 知识问答 | Agent 按需生成检索词、Dense + BM25、无权重 RRF、上下文压缩、多次检索、流式回答与引用 |
| Agent 编排 | 单个 LangChain `create_agent` 循环；模型可直接回答，或按需多次调用 `memory(query)` / `rag(query)` |
| 对话记忆 | Redis 短期记忆、会话增量摘要、PostgreSQL 长期记忆、默认启用的 Qdrant 语义索引 |
| 记忆治理 | 两阶段 LLM 候选提取/裁决、待审批、版本修订、软删除、恢复、永久删除、导出、索引校准与召回日志 |
| 管理与审计 | Agent trace、LLM 调用日志、检索日志、反馈、记忆事件、审计日志和管理指标 |

## 核心设计

### 受控 Agent 循环

- 使用单个 LangChain `create_agent` 循环，不设置独立意图分类器。模型每一步可以直接回答，也可以调用 `memory(query)` 或 `rag(query)`。
- 最近 user/assistant 对话以真实消息历史传入；动态 System Prompt 只承载核心画像、会话摘要、累计长期记忆、累计 RAG 证据和剩余预算。
- 完整工具结果保存在本轮 `AgentRunState`，后续模型调用重新获得累计上下文；原生 `ToolMessage` 只返回轻量回执，降低重复 token。
- 后端限制模型调用次数，并在每次模型调用前按总工具/分工具预算过滤可用工具；同时禁用并行工具调用，最后一次模型调用移除工具并强制收口。工具执行入口的二次硬预算保护仍属于已知限制。

### 权限感知的混合检索

```text
Agent query
→ knowledge-base / department / classification authorization
→ Dense(Qdrant) + BM25(PostgreSQL)
→ unweighted RRF
→ context compression and token budget
→ accumulated evidence with stable citation numbers
→ final answer
```

Dense 与 BM25 在候选阶段就应用相同权限过滤。PostgreSQL 保存文档、chunk 与权限真相，Qdrant 保存可重建向量；最终引用只来自实际提供给模型的授权 chunk。

### 分层记忆与两阶段写入

- 回答前固定加载核心 Profile、会话摘要和最近对话；普通长期记忆仅在 Agent 调用 `memory(query)` 时按需召回。
- 普通长期记忆默认使用 Qdrant 全量向量召回，并与 PostgreSQL 最近候选回退合并；同一轮多次召回按 Memory ID 去重，最终再受条数和 token 预算约束。
- 回答提交后，Candidate Extractor 只从当前 turn 提取候选，不接收旧记忆，也不能指定目标 ID。
- 每条候选都会经过 exact hash、canonical key/category、Qdrant 和 PostgreSQL 回退检索，再由独立的第二次 Memory Judge 判断 `independent/equivalent/refinement/replacement/uncertain/discard`，服务层机械映射为最终写入动作。
- 向量相似度只用于有界 top-K 排序，不设关系阈值，也不触发自动语义合并。`refinement/replacement` 继承目标记忆的注入层、槽位和 canonical key；只有 `independent` 按候选自身分类决定固定注入或按需召回。
- Judge 失败、目标非法或缺失时 fail-closed；通过后仍需接受 evidence、敏感信息、目标归属、exact hash、revision、唯一约束和事务校验。
- PostgreSQL 始终是长期记忆权威源；Qdrant 写入是 best-effort，Celery Beat 每日修复 missing/stale memory point。

Celery durable job、幂等键、租约 fencing、指数退避和 Beat 扫描恢复覆盖主要异步失败窗口。SSE 会话保留 Agent、检索、LLM 与消息之间的 provenance，统一质量门禁覆盖迁移、后端回归、Python 编译、前端构建和 Compose 校验。

## 架构

```mermaid
flowchart LR
    Browser[React + Vite] --> API[FastAPI API]
    API --> Services[Service / Authorization]
    Services --> Agent[LangChain create_agent Loop]
    Agent --> RAG[RAG Pipeline]
    Agent --> Memory[Memory Pipeline]

    Agent --> LLM[OpenAI-compatible LLM]
    Services --> PG[(PostgreSQL: source of truth)]
    Services --> Redis[(Redis: cache / broker)]
    Services --> MinIO[(MinIO: source files)]
    RAG --> PG
    RAG --> Qdrant[(Qdrant)]
    Memory --> PG
    Memory --> Redis
    Memory --> Qdrant
    RAG --> Embedding[OpenAI-compatible Embedding]
    Memory --> Embedding
    Memory --> LLM

    Redis --> Worker[Celery Worker]
    Beat[Celery Beat] --> Redis
    Worker --> PG
    Worker --> MinIO
    Worker --> Qdrant
```

系统包含三条主要数据链路：

1. **文档入库**：上传原文到 MinIO，保存文档元数据，由 Celery 解析、切分、生成向量，并写入 PostgreSQL 与 Qdrant。
2. **对话执行**：提交用户消息，固定加载核心画像与获准的会话上下文；模型直接回答或按需多次调用 Memory/RAG，直到信息充分或达到预算。
3. **回答后记忆更新**：第一阶段只提取候选，系统按候选检索 Qdrant 与 PostgreSQL 旧记忆，第二阶段逐条裁决，最后由后端确定性规则和数据库事务决定是否落库。

完整时序、事务边界、失败语义和数据模型见 [Agent 与记忆模块深度设计](docs/agent_memory_deep_dive.md)。

## 评估与可复现结果

以下仅展示 2026-07-15 完整重跑中稳定、口径清晰且适合公开呈现的结果。完整配置、判分规则、模型信息、生产链路诊断和输出文件见 [项目量化结果、评测标准与简历表述](docs/resume_metrics.md)。子集结果和 LLM Judge 结果不能当作公开榜单成绩。

| 评测 | 数据范围 | 当前结果 | 衡量内容 |
|:--|:--|:---|:--|
| BEIR/SciFact | 完整 test：5,183 篇语料、300 条 query | Hybrid nDCG@10 **68.47%**、Recall@10 **83.22%**、MRR@10 **64.62%** | Dense/BM25/RRF 检索排序 |
| Agent trajectory | 25 个项目 golden cases × 3 轮 | 严格轨迹 **98.67%（74/75）**、工具类型 **100%** | 直接回答、Memory/RAG 选择与多次检索 |
| RGB-derived Reader | 100 题分层子集 | 综合成功率 **84%**、官方答案命中 **88%**、引用精确率 **99%** | 给定文档后的读取鲁棒性 |
| LongMemEval-S Reader | 30 题、14,841 个历史 turn | Turn Recall Any@5 **95.83%**、Recall All@5 **83.33%**、Top-5 QA **83.33%** | 单次长期记忆检索与读取 |
| 工程质量门禁 | 生产容器依赖与真实 Qdrant | 后端回归 **310/310**、24 个 Alembic revision、前端生产构建与端到端 smoke 全部通过 | 工程可靠性与可部署性 |

主要复现入口：

```bash
python scripts/benchmark_beir_scifact.py --embedding-workers 8 --route-limit 15
python scripts/evaluate_agent_trajectory.py --workers 4
python scripts/evaluate_rgb_reader.py --per-task 25 --workers 4
python scripts/evaluate_longmemeval_memory.py --per-type 4 --abstention 6
```

`evaluate_rgb_agent_runtime.py` 和 `evaluate_longmemeval_agent_runtime.py` 会走真实生产 Agent/数据库/Qdrant 链路，必须使用隔离的评估数据库与独立 Qdrant collection。不要对生产数据直接运行带 `--allow-database-seed` 的评估命令。

## 技术栈

- 后端：Python 3.12、FastAPI、SQLAlchemy 2、Alembic、Pydantic Settings、LangGraph、LangChain、Celery
- 前端：React 18、TypeScript、Vite、React Router、React Markdown、Nginx
- 基础设施：PostgreSQL 16、Redis 7、Qdrant、MinIO、Docker Compose
- 模型接口：OpenAI-compatible Chat Completions 与 Embeddings

## 前置条件

- Git
- Docker Engine 或 Docker Desktop，支持 Docker Compose v2
- 可访问的 OpenAI-compatible LLM API
- 可访问的 OpenAI-compatible Embedding API
- 执行本地质量门禁时还需要 Python 3.12、Node.js 22.12+ 和 npm

LLM 与 Embedding 不要求来自同一供应商。开发模板通过两组独立配置连接模型服务：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 与 `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL`。

## 快速启动

### 1. 创建本地配置

PowerShell：

```powershell
Copy-Item .env.example .env
```

Bash：

```bash
cp .env.example .env
```

至少检查并替换以下模型配置：

```dotenv
LLM_BASE_URL=https://your-llm-service.example/v1
LLM_API_KEY=replace-me
LLM_MODEL=your-chat-model

EMBEDDING_BASE_URL=https://your-embedding-service.example/v1
EMBEDDING_API_KEY=replace-me
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_DIMENSION=1024
```

`EMBEDDING_DIMENSION` 必须与模型实际输出一致。修改模型或维度后，需要重建对应的 Qdrant collection；文档向量需要重新入库，Memory point 可通过 reconcile 重新生成，不能把旧维度向量直接沿用。

### 2. 启动开发环境

```bash
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

| 服务 | 地址 |
|---|---|
| Web 应用 | http://localhost:5173 |
| 后端 API | http://localhost:8000/api |
| OpenAPI 文档 | http://localhost:8000/docs |
| 存活检查 | http://localhost:8000/api/health |
| 就绪检查 | http://localhost:8000/api/ready |
| Qdrant | http://localhost:6333 |
| MinIO Console | http://localhost:9001 |

停止服务：

```bash
docker compose -f infra/docker-compose.yml down
```

## 首次演示

1. 打开 http://localhost:5173 并注册用户。空数据库中的首个用户会成为引导管理员。
2. 创建一个私有知识库，例如“企业制度演示知识库”。
3. 上传 [demo/company_policy_demo.md](demo/company_policy_demo.md)，等待文档状态变为 `indexed`。
4. 在对话页面选择该知识库并提问：`住宿报销上限是多少？`
5. 检查回答引用、检索日志、Agent trace 和记忆面板，确认回答证据可追踪。

也可以在服务运行后执行自动化冒烟演示。脚本会创建临时用户和知识库，上传文档、提问、验证引用，并默认清理知识库：

```bash
python scripts/smoke_demo.py
```

## 配置

- [`.env.example`](.env.example)：本地开发模板，包含可运行的开发默认值和参数注释。
- [`.env.production.example`](.env.production.example)：生产参考模板，敏感项均为待替换占位符。
- [`config.py`](apps/backend/app/core/config.py)：后端配置类型、默认值、范围及跨参数校验的权威定义。

主要配置组包括应用与 CORS、PostgreSQL 连接池、Redis、Qdrant、MinIO、LLM、Embedding、Agent 并发与超时、混合检索、上下文压缩、短期/长期记忆、增量摘要、Celery 恢复、数据保留、文档切分和认证。

当前模板中的关键运行默认值：

| 配置 | 开发 / 生产模板 | 说明 |
|---|---:|---|
| `AGENT_MAX_MODEL_CALLS` | 6 | 单轮模型调用硬上限，最后一次移除工具并收口 |
| `AGENT_MAX_TOOL_CALLS` | 4 | 单轮 Memory + RAG 声明预算；模型调用前据此解绑工具 |
| `AGENT_MAX_MEMORY_CALLS` / `AGENT_MAX_RAG_CALLS` | 2 / 3 | 分工具声明预算；执行入口二次硬校验仍待补充 |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_ROUTE_LIMIT` | 6 / 15 | 最终证据数 / 每条检索路线候选深度 |
| `SHORT_MEMORY_MAX_MESSAGES` | 16 | 最近消息缓存窗口 |
| `MEMORY_VECTOR_INDEX_ENABLED` | true | 回答召回和更新前相关记忆召回均使用 Qdrant |
| `MEMORY_SEMANTIC_LIMIT` | 6 | 单次普通长期记忆召回上限 |
| `MEMORY_CONTEXT_MAX_LONG_MEMORIES` | 10 | 一轮累计结果进入格式化阶段的普通长期记忆上限 |
| `MEMORY_CONTEXT_MAX_TOKENS` | 1600 | 完整 Memory context 独立预算 |
| `MEMORY_UPDATE_MODE` | sync / async | 开发模板同步写入，生产模板使用 durable Celery job |

环境变量在进程启动时读取。修改后应重启 backend、worker 和 Beat。不要提交本地 `.env` 或任何真实凭据。


默认 Compose 流程假设配置文件是项目根目录的 `.env`。如果使用其他 `--env-file`，还必须将 `APP_ENV_FILE` 设为该文件相对 `infra/*.yml` 的路径，确保 Compose 插值与 backend/worker 读取同一份配置。

## 本地测试

安装后端和前端依赖：

```bash
python -m pip install -e apps/backend
npm --prefix apps/frontend ci
```

执行本地核心质量门禁：

```bash
python scripts/check_project.py
```

该命令当前运行 310 项后端回归、Python `compileall`、完整 Alembic 迁移验证、TypeScript/Vite 构建，以及开发与生产 Compose 配置校验。2026-07-15 在生产容器依赖和真实 Qdrant 下完整回归为 **310/310**；GitHub Actions 还会实际构建生产前端镜像，执行 Nginx 参数 preflight 和 `nginx -t`。服务已启动时可追加端到端冒烟测试：

```bash
python scripts/check_project.py --with-smoke
```

## 生产部署参考

生产 Compose 是单机部署参考，不是完整的平台化交付：

```bash
cp .env.production.example .env
docker compose -f infra/docker-compose.prod.yml --env-file .env config --quiet
docker compose -f infra/docker-compose.prod.yml --env-file .env up --build -d
```

启动链路会先执行 Alembic migration，再启动 backend、worker、单个 Beat 调度器和 Nginx 前端。详细说明见 [生产部署](docs/production_deployment.md)。

上线前至少完成以下工作：

- 使用密钥管理服务注入 PostgreSQL、MinIO、JWT、LLM 与 Embedding 凭据，并建立轮换流程。
- 在受控入口部署 TLS、精确 CORS、边缘限流/WAF；不要直接暴露数据库、Redis、Qdrant 或 MinIO 管理端口。
- 只向外发布前端/Nginx 入口，保持 backend 在内部网络，防止绕过代理限制与安全策略。
- 将刷新令牌迁移到 `Secure`、`HttpOnly`、适当 `SameSite` 的 Cookie，并评估 SSO/OIDC、MFA 与管理员治理。
- 建立集中日志、指标、分布式追踪、告警、审计归档和敏感数据脱敏。
- 为 PostgreSQL、MinIO、Qdrant 和 Redis 制定备份、恢复演练、保留周期和跨区域灾备策略。
- 固定并扫描容器镜像与依赖版本，补充 SBOM、漏洞扫描、文件恶意内容检测和供应链策略。
- 在真实基础设施上完成权限、并发、队列恢复、负载、故障注入和数据恢复测试。

## 目录结构

```text
apps/backend/app/   FastAPI、Agent、RAG、记忆、服务、数据模型与 Worker
apps/backend/tests/ 后端测试与回归测试
apps/frontend/src/  React 页面、组件、API client 与 SSE 处理
infra/              开发和生产 Docker Compose
docs/               可公开的架构、模块、API、评估与部署文档
demo/               演示文档与 RAG 评估数据
scripts/            质量门禁、迁移校验、冒烟演示与评估脚本
```

## 公开文档

- [Agent 与记忆模块深度设计](docs/agent_memory_deep_dive.md)
- [Agent 编排](docs/agent_orchestration.md)
- [RAG Pipeline](docs/rag_pipeline.md)
- [API 参考](docs/api.md)
- [架构图](docs/architecture_diagrams.md)
- [Prompt 设计](docs/prompts.md)
- [RAG 评估](docs/evaluation.md)
- [项目量化结果、评测标准与简历表述](docs/resume_metrics.md)
- [生产部署](docs/production_deployment.md)
- [演示数据](demo/README.md)

开发模式允许 `AUTO_CREATE_TABLES=true` 方便本地运行；生产必须使用 `AUTO_CREATE_TABLES=false` 和 Alembic。

## 提醒

- 当前没有启用 cross-encoder reranker；每次 `rag(query)` 使用 Dense、BM25 与无权重 RRF 融合。
- Qdrant Memory index 是 best-effort 派生索引；不可用时只回退到有界 PostgreSQL 候选，极旧且不在候选窗口中的普通记忆可能漏召回。
- 记忆只用于用户偏好、风格和对话连续性，不能作为企业事实证据。
- Agent 会在模型调用前移除已耗尽预算的工具，但工具执行入口尚未实施第二道硬预算校验；兼容模型若重复输出历史 tool call，可能越过声明的分工具预算，详见量化结果文档。
- Agent 流式并发限制按 Uvicorn 进程生效，不是跨副本的全局配额。
- 引用表示提供给模型的证据集合，不等同于逐句事实核验。
- 开发环境的首用户管理员机制仅适用于单实例引导；生产需要明确的管理员生命周期。
- 当前前端使用 `localStorage` 保存令牌；公网生产环境必须改造令牌存储与浏览器安全策略。
- 生产 Compose 面向单机参考场景，未提供多区域高可用、自动扩缩容或托管云资源编排。

## 许可证

MIT
