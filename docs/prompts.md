# Prompt 说明

当前 Prompt 体系只覆盖三个真实职责：Agent 工具循环、会话/上下文压缩，以及长期记忆编辑。项目没有独立意图分类、Query Rewrite、Writing Agent 或最终 answer tool。

## 总体原则

- 工具调用可选；上下文足够时直接回答。
- 用户个人事实只能来自核心画像、会话上下文和 Memory 结果。
- 企业事实只能来自 RAG 证据，并保留 `[1]`、`[2]` 等引用。
- Memory 不能替代企业知识库，Memory miss 不能触发到 RAG 搜索个人信息。
- 记忆、文档、问题和工具观察全部视为不可信数据，不能覆盖 system rules。
- 权限、工具集合、调用预算、重复检测和最终收口由服务端执行，Prompt 只表达决策准则。

## Agent 动态 System Prompt

主要实现位置：`apps/backend/app/agents/runtime.py`。

每次模型调用都会重新构造 Prompt，包含：

| 区块 | 用途 |
|---|---|
| Tool rules | 说明 `memory(query)` 与 `rag(query)` 的证据边界和重试规则 |
| Answer rules | 规定个人/企业事实来源、引用和证据不足时的表达 |
| Available tools | 只列出本回合真正仍可调用的工具 |
| Remaining budgets | 模型、总工具、Memory 与 RAG 的剩余次数 |
| Previous queries | 防止重复或同义词式无效重试 |
| User context | 固定核心画像、会话摘要、最近对话及已召回普通记忆 |
| RAG evidence | 所有 RAG 批次累计的带稳定编号证据 |

最后一次模型调用和总工具预算耗尽后的调用不提供工具，Prompt 明确要求立即给出最终回答；如果 Memory 或知识库仍不足，必须说明缺少哪类依据。

## 工具描述

`memory(query)` 的描述强调它只搜索当前用户的普通长期记忆，例如项目、决策、事件和工作流；已经注入的核心画像不应重复检索，企业制度也不能从 Memory 回答。

`rag(query)` 的描述强调它只搜索后端已经授权的企业知识库范围。query 应简洁并只针对一个未解决的信息需求；模型可以在下一回合用不同 query 再查。

## 会话摘要与上下文压缩

主要实现位置：

- `apps/backend/app/llm/provider.py`
- `apps/backend/app/llm/context_compression.py`
- `apps/backend/app/services/memory_service.py`

会话摘要只用于更新 `conversations.summary`，不是最终回答。Memory 压缩必须保留受保护的核心画像 source ID；RAG 压缩只能从原 chunk 逐字抽取，并校验 chunk ID、原文包含关系和 token 上限。验证失败时回退确定性裁剪。

## 长期记忆编辑

主要实现位置：

- `apps/backend/app/services/memory_service.py`
- `apps/backend/app/llm/provider.py`

编辑器接收核心画像、与当前用户消息语义相关的普通记忆、pending 候选以及当前 user/assistant turn。它只提出 `create/update/supersede/pending/ignore`；证据归属、敏感信息、冲突、去重、乐观并发和最终数据库操作仍由服务层决定。

核心画像只包括姓名、称呼、当前角色、语言、响应详略、格式、语气、无障碍偏好和明确的全局指令。公司、团队、背景、项目、技术栈、决策、事件、任务和普通工作流均为按需长期记忆。

## 修改规则

1. 先补或更新行为测试，尤其是工具选择、预算、引用、权限和记忆冲突。
2. 不把权限或事实校验下放给 Prompt。
3. 不重新引入意图分类、查询改写或并行工具调用。
4. 模型不可用时暴露错误，不用本地规则伪造回答。
5. 修改后运行后端全量测试、前端 build、Alembic head 检查和真实链路 smoke test。
