# Prompt 说明

本项目的 Prompt 策略保持克制：Prompt 只服务当前 RAG、Agent、记忆和写作流程，不做复杂模板系统。所有模型输出都来自配置的真实 LLM provider。

## 总体原则

- 只基于知识库上下文回答事实问题。
- 记忆上下文只用于理解用户偏好和会话状态，不作为事实依据。
- 上下文不足时必须说明依据不足。
- 回答需要保留引用标记，例如 `[1]`、`[2]`。
- Agent 节点复用现有 RAG service，不重复实现检索逻辑。
- LLM 可以提出路由和记忆候选，但服务层负责最终归一化、过滤和落库。

## RAG 回答

主要实现位置：

- `apps/backend/app/rag/answering.py`
- `apps/backend/app/llm/provider.py`

回答 Prompt 的核心约束：

```text
You are an enterprise knowledge assistant.
Answer only from the provided context.
Use citation markers such as [1].
Use the memory context only to understand the user and the conversation style;
never treat memory as knowledge-base evidence.
Say when the knowledge context is insufficient.
```

输入被拆成三块：

| 区块 | 用途 |
|---|---|
| Question | 用户问题 |
| Memory and conversation context | 用户偏好、短期上下文、会话摘要 |
| Knowledge context | 检索出的 chunk，带 `[n]` 引用序号 |

回答始终由兼容 OpenAI Chat Completions 的 LLM provider 生成；当 `Knowledge context` 为空或不足时，提示词要求模型明确说明知识库依据不足，不得用通用知识补答。

## Supervisor Agent

实现位置：

- `apps/backend/app/agents/supervisor.py`
- `apps/backend/app/llm/provider.py`

意图分类只允许三个标签：

| 标签 | 路由 |
|---|---|
| `rag` | 知识库问答 |
| `memory` | 回答“你记得我什么/我的偏好是什么”等用户记忆问题 |
| `chat` | 寒暄、感谢、无需检索的小对话 |
| `summary` | 摘要/归纳 |
| `writing` | 写作/起草 |

分类 Prompt 要求只返回标签。若真实 LLM 输出包含解释性文本，服务层会按包含关系归一化到允许的路由标签。服务层还会做保守裁决：企业事实、制度、流程、报销、合同等问题默认回到 `rag`；明确询问用户记忆时才走 `memory`；纯寒暄才走 `chat`。

## Summary

实现位置：

- `apps/backend/app/llm/provider.py`
- `apps/backend/app/services/memory_service.py`

摘要用于更新 `conversations.summary`，不是最终回答。触发条件由服务层控制，避免每轮对话都调用摘要。

约束：

- 保留用户目标、结论和关键业务背景。
- 不把未经确认的模型推断写成事实。
- 摘要长度会在写库前截断，防止无限增长。

## Writing

实现位置：

- `apps/backend/app/agents/writing_agent.py`
- `apps/backend/app/llm/provider.py`

写作节点用于“起草通知、报告、邮件”等请求。当前版本保持最小可运行：写作内容必须使用已有 grounding，不把写作节点变成独立知识源。

## Memory

实现位置：

- `apps/backend/app/services/memory_service.py`
- `apps/backend/app/llm/provider.py`

长期记忆抽取 Prompt 只提取 durable preference/profile/project/instruction，并要求返回 JSON array。没有可复用偏好时返回 `[]`。LLM 只提出候选，服务层根据置信度、敏感度、重复和冲突关系决定是否写入。

写入前还会经过服务层规则：

| 动作 | 场景 |
|---|---|
| `create` | 新的稳定偏好 |
| `touch` | 精确重复或同向偏好 |
| `merge` | 语义相似且互补 |
| `supersede` | 与旧偏好冲突 |
| `pending` | 置信度不足或敏感度不低，等待用户审核 |
| `ignore` | 临时问题、闲聊、用户明确要求不要记住 |

`pending` 记忆不会进入回答上下文。只有 `active` 记忆会参与记忆召回；回答风格、语言、格式类记忆会被优先带入，普通背景记忆需要和当前问题有足够语义相关性。

## 调整 Prompt 的规则

1. 先补或更新测试，尤其是权限、引用、记忆去重和 Agent trace。
2. 不扩大 Prompt 职责；检索、融合、重排仍在 RAG pipeline 中完成。
3. 不让记忆上下文成为事实来源。
4. 模型不可用时应暴露错误，不能新增本地规则回答兜底。
5. 修改后至少运行：

```bash
python scripts/check_project.py --with-smoke
```
