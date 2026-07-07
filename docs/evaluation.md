# RAG 评估说明

本项目的评估目标不是替代人工验收，而是给 RAG 检索和引用质量一个可重复的弱信号。评估脚本会登录后端、逐条调用 `/api/knowledge-bases/{kb_id}/ask`，再根据返回答案和引用计算指标。

## 前置条件

1. 启动本地服务：

```bash
docker compose -f infra/docker-compose.yml --env-file .env.example up -d --build
```

2. 注册或准备一个用户。
3. 创建知识库并上传 `demo/company_policy_demo.md`。
4. 等待文档状态变为 `indexed`，且 `chunk_count > 0`。
5. 记录知识库 id。

可先运行端到端冒烟，确认主链路可用：

```bash
python scripts/smoke_demo.py
```

## 数据集格式

默认数据集是 `demo/rag_eval_questions.json`：

```json
{
  "cases": [
    {
      "id": "travel_hotel_limit",
      "question": "住宿报销上限是多少？",
      "expected_sources": ["company_policy_demo.md"],
      "expected_keywords": ["600", "450", "住宿"]
    }
  ]
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `id` | 用例标识，便于定位失败样本 |
| `question` | 发给问答接口的问题 |
| `expected_sources` | 期望引用命中的文件名 |
| `expected_keywords` | 期望答案中出现的关键词 |

## 运行命令

推荐使用计划兼容入口：

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

`scripts/run_eval.py` 只是稳定入口，内部复用 `scripts/evaluate_rag.py`。

如果已经有 access token，也可以跳过登录：

```bash
python scripts/run_eval.py --token <access_token> --kb-id <knowledge_base_id>
```

## 指标解释

| 指标 | 含义 |
|---|---|
| `recall_at_k` | 前 K 个引用中是否命中任一预期来源 |
| `mrr` | 第一个正确引用的倒数排名，越高越好 |
| `citation_hit_rate` | 每条样本是否至少有一个预期来源引用 |
| `answer_keyword_hit_rate` | 答案是否包含预期关键词，只作为弱信号 |

这些指标只验证“检索和引用是否大致命中”。答案是否完整、是否可直接交付，仍需结合 `docs/manual_acceptance_checklist.md` 做人工验收。

## 失败排查

| 现象 | 常见原因 | 排查 |
|---|---|---|
| 登录失败 | 用户不存在或密码不匹配 | 先在前端注册，或使用 `--token` |
| 没有引用 | 文档未入库、知识库 id 错误、检索过滤不匹配 | 检查文档状态、`/api/documents/{id}/chunks` 和 retrieval logs |
| 关键词命中低 | LLM 可能改写检索片段措辞，关键词不一定逐字保留 | 优先看引用指标，再调整关键词 |
| MRR 偏低 | 多路召回候选顺序不稳定 | 查看 retrieval logs 的 routes 和 RRF 分数 |

## 发布前建议

发布或展示前至少跑：

```bash
python scripts/check_project.py --with-smoke
python scripts/run_eval.py --email <email> --password <password> --kb-id <knowledge_base_id>
```
