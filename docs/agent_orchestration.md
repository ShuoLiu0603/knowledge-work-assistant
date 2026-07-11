# Agent 编排说明

本项目的 Agent 编排目标是把不同意图路由到合适节点，同时复用已经稳定的 RAG service。

## AgentState

`AgentState` 保存一次 Agent run 的关键状态：

- 用户、知识库、会话和消息 id。
- 用户输入和判断出的 intent。
- 短期记忆、长期记忆和会话摘要。
- RAG answer、citations、retrieval_log_id。
- memory_actions。
- trace、status 和 error_message。

## 节点职责

### Memory Agent

- 回答前加载 Redis 短期记忆、`conversations.summary` 和相关长期记忆。
- 回答后处理用户输入中的长期偏好。
- LLM 提出 `create`、`update`、`supersede`、`pending` 或 `ignore`；服务层还可根据去重与冲突结果记录 `touch`、`merge` 等派生 action。
- 异步模式先返回 deferred/queued 状态，最终写入由 durable job 完成。

### Supervisor Agent

- 使用 LLM Provider 判断意图。
- 输出 `rag`、`memory`、`chat`、`summary` 或 `writing`。
- 明确的 full-memory-recall 标记先于 LLM 分类，确定性路由到 `memory`。
- 其他请求依赖分类 Prompt 与结构化标签；无法归一化的输出回退为 `rag`，当前不再根据原始用户文本做第二次语义规则裁决。
- 不直接回答问题。

### RAG Agent

- 调用已有 RAG service。
- 返回 grounded answer 和 citations。

### Memory Answer

- 当用户询问“你记得我什么/我的偏好是什么”等问题时，不检索知识库。
- 只基于长期记忆、短期记忆和会话摘要回答。
- 不返回知识库 citations。

### Chat

- 用于寒暄、感谢等无需检索的轻量对话。
- 不回答企业事实问题。
- 不重写检索流程。

### Summary Agent

- 调用 RAG service 获取依据。
- 使用 LLM Provider 生成摘要。
- 结合记忆上下文做基础个性化。

### Writing Agent

- 调用 RAG service 获取依据。
- 使用 LLM Provider 生成草稿。
- 仍然保留引用和 trace。

## Trace 设计

每个节点向 `agent_runs.trace` 写入：

- `node`：节点名称。
- `action`：执行动作。
- `input`：关键输入摘要。
- `output`：关键输出摘要。

这样可以在前端或接口中解释一次回答为什么进入某个 Agent、加载了多少记忆、是否触发记忆更新。

## 当前边界

- 不实现完整自主规划。
- 不新增工具调用市场。
- 不让 Agent 绕过权限校验。
- 不让 Memory Agent 替代 RAG 检索。
