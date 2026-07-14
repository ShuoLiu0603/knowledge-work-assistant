# Agent 编排说明

本项目使用一个 LangChain `create_agent` 循环。模型不经过独立意图分类器：当前上下文足够时直接输出最终回答；信息不足时可以按需、多次调用 `memory(query)` 或 `rag(query)`。

## AgentRunState

`AgentRunState` 保存一次运行的关键状态：

- 用户、知识库授权范围、会话和消息 ID。
- 用户输入、核心画像、会话摘要与最近对话。
- 按需召回的普通长期记忆、Memory query 与召回观察。
- 多批 RAG query、累计 chunk、稳定引用和全部 RetrievalLog ID。
- 模型、总工具、Memory、RAG 调用计数。
- memory actions、trace、status、deadline、取消信号和错误信息。

## 执行循环

```text
加载核心画像与会话上下文
→ 模型判断
  → 信息充分：直接回答
  → 缺少用户长期信息：memory(query) → 重新判断
  → 缺少企业文档证据：rag(query) → 重新判断
→ 回答后执行或投递长期记忆更新
```

模型每回合只能执行一个工具调用。默认上限是 6 次模型调用、4 次工具调用，其中 Memory 最多 2 次、RAG 最多 3 次；最后一次模型调用不再提供工具，必须基于已有信息回答或明确说明证据不足。

## `memory(query)`

- 只搜索当前用户的普通长期记忆，例如项目、技术栈、历史决策、事件和工作流。
- 核心姓名、称呼、当前角色、语言及稳定响应偏好已经固定注入，无需再次搜索。
- 不搜索企业知识库，不能作为制度或文档事实证据。
- 同一标准化 query 不重复执行。

## `rag(query)`

- 只搜索服务层预先解析并授权的知识库范围；模型不能改变 KB、部门或密级。
- 每次调用接受一条模型生成的独立 query，执行 Dense + BM25 + RRF。
- 模型可以根据首轮结果换一个实质不同的 query 再查。
- 每次调用产生独立 RetrievalLog；所有批次的证据与日志都会保留并关联到最终消息。
- 企业声明必须使用累计证据中的稳定数字引用。

## Trace

每一步向 `agent_runs.trace` 写入 `node`、`action`、`input` 和 `output`。主要动作包括：

- `load_core_context`
- `call_tools` / `respond`
- `memory` / `rag`
- `final_answer`
- `defer_user_memories` / `update_user_memories`
- `complete` / `cancel` / `timeout` / `error`

前端按模型步骤、工具 query、结果数量和最终状态展示轨迹，不再展示 intent。

## 安全边界

- 工具内容按不可信数据处理，不能覆盖 system rules。
- 用户记忆和企业证据严格分离。
- 权限、预算、超时、重复调用、检索 provenance 与最终收口由后端执行。
- Memory miss 不允许转而到 RAG 搜索个人信息；企业证据不足时不得用模型常识猜测内部事实。
