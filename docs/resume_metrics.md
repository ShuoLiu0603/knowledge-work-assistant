# 项目量化结果、评测标准与简历表述

> 最近完整验证：2026-07-15  
> 模型：`deepseek-v4-flash`；Embedding：`text-embedding-v3`（1024 维）  
> 本文是项目唯一的量化结果文档。公开检索基准、Reader-only、受控 Agent 轨迹和真实项目运行时 Agent 是不同口径，不能混写。这里的“运行时 Agent”指未 mock 的当前生产代码链路，不代表真实业务生产流量。

## 可直接用于简历

### 检索与 Agent 编排

> 基于 FastAPI、PostgreSQL、Qdrant、Celery 与 LangChain 构建 Agentic RAG 系统；在 BEIR/SciFact 完整测试集（5,183 篇语料、300 条查询）上，Dense + BM25 + RRF 取得 nDCG@10 68.47%、Recall@10 83.22%、MRR@10 64.62%，nDCG@10 相比 Dense 提升 4.62 个百分点；25 个项目定制 golden cases 连续运行 3 轮，在 75 条轨迹上取得 98.67% 严格轨迹准确率、100% 工具类型准确率和 0% 直接回答误调用率。

### 长期记忆与工程质量

> 在 LongMemEval-S 30 题分层子集、14,841 个历史 turn 上实现 Turn Recall Any@5 95.83%、Recall All@5 83.33% 和 Top-5 Reader QA 83.33%；建立 310 项后端回归测试与统一质量门禁，完成 24 个 Alembic 版本的完整迁移链、真实 PostgreSQL/Qdrant/Redis/MinIO/Celery 冒烟及前端生产构建。

不能写“测试覆盖率 100%”：当前统计的是测试数量与通过率，没有生成语句或分支覆盖率报告。RGB 和 LongMemEval 的 DeepSeek Judge 结果也不能写成官方榜单成绩。

## 1. 本轮真实运行环境

除明确标注为 Reader-only 或受控工具 observation 的评测外，本轮生产链路使用：

- Docker Compose 中的 PostgreSQL 16、Qdrant、Redis 7、MinIO、FastAPI、Celery Worker/Beat 和前端。
- 真实 `deepseek-v4-flash` 与真实 embedding API，不 mock 模型。
- 生产 Agent、生产 Memory/RAG 工具、生产权限检查和生产检索实现。
- 数据库评测写入隔离的 `rag_app_eval`，Qdrant 使用名称包含 `eval` 的独立集合；脚本结束后清理临时数据。
- 主栈端到端 smoke 只创建临时知识库；知识库和精确匹配的 smoke 用户均在运行后删除。

## 2. BEIR/SciFact 公开检索基准

完整 test split：5,183 篇语料、300 条查询、339 个相关性标注。每篇 SciFact abstract 作为一个文档级检索单元；`RETRIEVAL_ROUTE_LIMIT=15`，`RRF_K=60`。

| 路线 | nDCG@10 | MAP@10 | Recall@10 | Precision@10 | MRR@10 |
|---|---:|---:|---:|---:|---:|
| Dense | 63.85% | 59.09% | 76.91% | 8.70% | 60.48% |
| BM25 | 66.68% | 62.20% | 78.94% | 8.80% | 63.44% |
| Dense + BM25 + RRF | **68.47%** | **63.04%** | **83.22%** | **9.33%** | **64.62%** |

Hybrid 相比 Dense：nDCG@10 +4.62、Recall@10 +6.31、MRR@10 +4.13 个百分点。300 条查询的本地排序阶段平均 1,274.85 ms、P50 1,225.08 ms、P95 2,215.76 ms；该延迟不包含远程 query embedding，不能表述为完整 API 延迟。

判分是文档 ID 排序与公开 qrels 的确定性比较，不使用字符答案匹配或 LLM Judge。`Precision@10` 较低主要因为多数 query 只有约 1 个相关文档，却固定返回 10 个候选，并不等价于答案准确率低。

## 3. Agent 工具轨迹

25 个项目定制 case 覆盖直接回答、Profile 直接回答、Memory、Memory miss、RAG、Memory+RAG 和不同 query 的多次 RAG。Memory/RAG observation 受控，因此该评测隔离“工具调度能力”，不测真实召回。

| 指标 | 三轮合计 |
|---|---:|
| 严格轨迹准确率 | **98.67%（74/75）** |
| 工具类型集合准确率 | **100.00%（75/75）** |
| 工具多重集合/调用次数准确率 | 98.67%（74/75） |
| 直接回答误调用率 | **0.00%（0/24）** |
| 完成率 / 声明预算合规率 | 100.00% / 100.00% |
| 平均模型调用 / 工具调用 | 1.9067 / 0.9067 |
| 平均额外工具调用 | 0.0267 |

三轮分别为 100%、96%、100%。唯一失败是 Memory+RAG 已有足够证据后又调用两次 RAG；最终答案正确，但严格轨迹判错。

## 4. RGB-derived

100 题分层子集，每类 25 题。RGB 数据为 CC BY-NC-SA 4.0，仅限非商业使用。

### Reader-only

Reader 直接获得 5 篇外部文档，每题只生成一次，不运行项目 Agent 或检索器。

| 指标 | 结果 |
|---|---:|
| 四类综合成功率 | **84.00%** |
| RGB 官方答案包含命中率 | **88.00%** |
| 规范化答案匹配率 | 90.00% |
| Negative Rejection | 92.00% |
| Factual Error Detection | 92.00% |
| 检测成功后的纠错率 | 73.91% |
| 引用精确率 / 证据组覆盖率 | **99.00% / 92.00%** |
| 非法引用率 | 0.00% |

| 类别 | 成功率 |
|---|---:|
| Noise Robustness | 100.00% |
| Negative Rejection | 92.00% |
| Information Integration | 76.00% |
| Counterfactual Robustness | 68.00% |

## 5. LongMemEval-S 长期记忆

稳定分层子集共 30 题：六类非拒答题各 4 题，加 6 个 abstention；完整干扰历史 14,841 个 turn。使用 LongMemEval 官方分题型 Judge prompt，但 Judge 模型是 DeepSeek，不是官方指定的 `gpt-4o-2024-08-06`。

### 5.1 单次 Top-5 Memory 检索 + Reader

| 检索指标（24 个非拒答题） | 结果 |
|---|---:|
| Turn Recall Any@5 | **95.83%** |
| Turn Recall All@5 | 83.33% |
| 相关 turn 平均召回 | 88.33% |
| Turn nDCG@5 | 78.91% |
| Session Recall Any@5 / All@5 | 100.00% / 95.83% |

| QA 指标 | Retrieved Top-5 | Oracle |
|---|---:|---:|
| 全部 30 题准确率 | **83.33%** | 90.00% |
| 六类任务宏平均 | 79.17% | 87.50% |
| Abstention | 100.00% | 100.00% |

仅 1 题属于明确检索 miss，另有 3 题在相关 turn 全覆盖时仍失败，表明偏好理解、时间推理和证据使用同样是瓶颈。

### 5.2 真实项目运行时 Agent 的多次 Memory

原始日期化 turn 写入隔离 PostgreSQL 和独立 Memory Qdrant 集合；Memory 与 RAG 都真实可用。评测用户没有知识库，所以误调 RAG 会沿生产链路返回空证据。

| 指标 | 默认预算 6/4/2/3 | 上限探测 20/20/10/10 |
|---|---:|---:|
| 全部 30 题 QA | **76.67%** | 80.00% |
| 六类任务宏平均 | 70.83% | 75.00% |
| Abstention | 100.00% | 100.00% |
| Memory Recall Any / All | **91.67% / 70.83%** | 87.50% / 75.00% |
| 相关 turn 平均召回 | 80.83% | 81.67% |
| 平均 Memory 调用 | 1.40 | 1.70 |
| 二次 / 三次 Memory 调用率 | **43.33% / 3.33%** | 40.00% / 13.33% |
| 多次调用时 query 变化率 | 100.00% | 100.00% |
| Memory 无进展调用率 | 13.33% | 10.00% |
| 平均 token | **5,139** | 6,289 |
| 平均端到端延迟 | **39.59 秒** | 48.29 秒 |
| 运行失败 | 0/30 | 0/30 |

上限组比默认组净增 3.33 个百分点，但配对结果是 3 题改善、2 题退化，只有 1 题能明确归因于超过默认预算后的额外调用。代价是平均 token +22.38%、延迟 +21.98%。因此当前证据不支持无条件提高最大步骤；默认组已经会在 43.33% 的题上二次调用 Memory。

默认组按配置声明 Memory 上限为 2，但观察到 1 题执行 3 次 Memory。原因是当前只在下一次模型调用前解绑耗尽预算的工具，工具执行入口没有第二道硬校验；该生产问题本轮未修改。

分类型上，默认预算的 Multi-session 为 25%（1/4），上限组为 75%（3/4）；Temporal 为 50%→75%，Preference 为 75%→25%。样本每类只有 4 题，且 Agent 温度为 0.2、回答与 Judge 属于同一模型族，不应把单轮变化当作稳定提升。

## 6. 在线 API 与工程质量门禁

| 检查项 | 2026-07-15 结果 |
|---|---|
| 后端完整回归（生产容器依赖、真实 Qdrant） | **310/310 通过**，233.58 秒 |
| 评测脚本静态回归 | 4/4 通过 |
| Python `compileall` | 通过 |
| Alembic 完整迁移链 | 24 revisions 通过，head=`20260713_0024` |
| 真实 PostgreSQL 主库 / `rag_app_eval` | 均已升级到 head |
| 前端 TypeScript + Vite build | 通过，534 modules transformed |
| 开发 / 生产 Compose config | 均通过 |
| `/api/health`、`/api/ready` | HTTP 200；database/redis/qdrant/minio/worker 全部 ok |
| 端到端 smoke | 注册→建库→上传→Celery 索引→4 chunks→问答→1 citation→清理，**27.49 秒** |

前端主 JS 为 586.29 kB、gzip 181.05 kB，Vite 仍提示主 chunk 超过 500 kB，不能表述为“前端性能已优化完成”。

宿主机直接运行统一门禁时为 309/310：唯一失败是宿主机无法解析 Docker 内部主机名 `qdrant`，导致外部清理审计断言失败；同一用例在 Docker 网络中连接真实 Qdrant 后通过，完整生产容器回归为 310/310。该差异属于运行位置配置，不是功能回归。

### 在线 3 题 RAG 小评测

使用真实 API、权限、Celery 入库和生产问答接口：

| 指标 | 结果 |
|---|---:|
| Recall@5 | 66.67% |
| MRR | 66.67% |
| Citation hit rate | 66.67% |
| Answer keyword hit rate | 100.00% |

该小评测用于验证真实 API、权限、异步入库和引用链路，不作为公开数据集成绩。

## 7. 结果边界与优化结论

- BEIR 数字只衡量检索排序，不衡量答案、权限、引用或 Agent 成功率。
- Agent trajectory 只衡量受控工具序列，不衡量真实 Memory/RAG 召回。
- RGB Reader-only 和 LongMemEval Reader-only 绕过生产 Agent，适合定位 Reader/检索上限，不能当作产品端到端成绩。
- Memory 已能多次检索并更换 query；下一步优先级应是路由/证据缺口判断、无进展停止和工具入口硬预算，而非简单扩大上下文或调用上限。
- 30 题 LongMemEval 与每类 4 题的子类统计样本较小；涉及 LLM-as-Judge 的百分点差异需要多轮重复或更大样本后再用于强结论。

## 8. 复现入口与本轮产物

主要命令：

```bash
python scripts/check_project.py
python scripts/benchmark_beir_scifact.py --embedding-workers 8 --route-limit 15
python scripts/evaluate_agent_trajectory.py --workers 4 --force
python scripts/evaluate_rgb_reader.py --per-task 25 --workers 4 --force
python scripts/evaluate_longmemeval_memory.py --per-type 4 --abstention 6 --force-reader
python scripts/evaluate_longmemeval_agent_runtime.py --per-type 4 --abstention 6 --budgets both --allow-database-seed
python scripts/smoke_demo.py --question "住宿报销上限是多少？"
python scripts/run_eval.py --email <email> --password <password> --kb-id <kb_id>
```

运行时 Agent 评测必须将 `DATABASE_URL` 指向名称包含 `eval` 的隔离数据库，并分别设置名称包含 `eval` 的知识库和 Memory Qdrant collection。禁止对生产数据运行带 `--allow-database-seed` 的命令。

本轮机器可读产物位于 `.run/p0/rerun_20260715/`：

- `beir_scifact_results.json`
- `agent_trajectory_round{1,2_utf8,3}_summary.json`
- `rgb_reader_summary.json`
- `longmemeval_summary.json`
- `longmemeval_agent_runtime_summary.json`
- `live_rag_eval_report.json`

## 9. 数据来源与许可证

- BEIR：<https://github.com/beir-cellar/beir>，论文 <https://arxiv.org/abs/2104.08663>。
- SciFact：<https://github.com/allenai/scifact>；claims/evidence 为 CC BY 4.0，abstract corpus 经 S2ORC 使用 ODC-By 1.0。
- RGB：<https://github.com/chen700564/RGB>，CC BY-NC-SA 4.0，仅限非商业用途。
- LongMemEval：<https://github.com/xiaowu0162/LongMemEval>，论文 <https://arxiv.org/abs/2410.10813>，代码 MIT。
- LongMemEval cleaned：<https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned>。
