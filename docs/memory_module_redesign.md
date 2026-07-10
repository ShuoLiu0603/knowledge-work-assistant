# Memory Module Redesign

This note records the target design for the project memory subsystem after the 2026-07-07 review.

## Current Judgment

The memory module is now a production-oriented foundation rather than the original MVP. The important design choices are:

- short-term and long-term memory are separated;
- only `active` long-term memory is recalled;
- low-confidence memories can be `pending`;
- superseded memories are kept instead of overwritten;
- memory context is explicitly separated from knowledge-base evidence.

The part that should not continue growing is the old single-file service shape. Memory editing, recall, policy, Redis cache, conversation summary, embeddings, and API CRUD should not live in one service module.

External designs support this direction:

- LangGraph separates thread-scoped short-term state from cross-thread long-term memory, and explicitly distinguishes semantic, episodic, and procedural memory. Its docs also call out the latency/quality tradeoff between writing memory on the hot path and writing it in the background: https://docs.langchain.com/oss/python/concepts/memory
- Letta treats stateful agents as persisted state made of messages, memory blocks, tools, and context; important core memories can be pinned in context while older state remains retrievable from storage: https://docs.letta.com/guides/core-concepts/stateful-agents
- LlamaIndex's newer memory model composes short-term chat history with long-term memory blocks, vector-backed memory, fact extraction, priorities, and token limits: https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/
- Mem0's product framing is useful for production expectations: add, extract/store, recall, with audit logs and workspace governance as first-class concerns: https://docs.mem0.ai/platform/overview
- OpenAI's ChatGPT memory controls reinforce user-facing requirements: users need review, correction, deletion, source explanations, and temporary/no-memory modes: https://help.openai.com/en/articles/8590148-memory-faq

## Target Layers

The best shape for this project is a layered memory subsystem:

1. Working memory
   - thread-scoped recent conversation state;
   - Redis is only a cache, PostgreSQL messages remain authoritative;
   - context formatting uses a token budget with a character cap so memory cannot grow unbounded in prompts.

2. Conversation state
   - thread-scoped summary, open tasks, and unresolved references;
   - summary updates use a processed-message cursor so earlier turns are not dropped.

3. Long-term user memory
   - cross-thread durable facts about user preferences, profile, projects, and long-term instructions;
   - low-sensitivity, high-confidence facts can become `active`;
   - uncertain facts go to `pending`;
   - sensitive facts require explicit user action.

4. Memory retrieval
   - sticky preferences such as language, format, and response detail are always considered;
   - other memories are selected by semantic relevance, recency, importance, and status;
   - retrieval writes recall logs for evaluation and aggregate quality metrics.

5. Governance
   - deletion is soft delete, not physical delete;
   - durable mutations write append-only memory events;
   - users should be able to inspect, approve, reject, delete, and export memories.

## Implemented Foundation

The code now has these module boundaries:

- `app.memory.policy`: thresholds, statuses, recall markers, category inference, and write-safety rules.
- `app.memory.short_term`: Redis-backed short-term memory cache and DB fallback helpers.
- `app.memory.context`: memory context formatter.
- `app.memory.retrieval`: sticky/semantic recall, dedupe, and cosine similarity.
- `app.memory.repository`: SQL query helpers for memory rows.
- `app.memory.events`: append-only memory mutation events.
- `app.memory.commands`: durable memory row mutations with event recording.
- `app.memory.embedding`: memory embedding adapter.
- `app.memory.editor`: long-term memory candidate and operation editor.
- `app.memory.vector_index`: optional Qdrant index for long-term memory, filtered by `user_id` and `status`.
- `user_memory_recall_logs`: append-only recall observability for selected and rejected memories.
- `/api/memories/recall-metrics`: user-scoped recall-quality aggregates over recall logs.
- `user_memory_update_jobs`: durable background memory update jobs for async editing.
- `app.memory.jobs`: job creation and dispatch helper.
- `app.workers.memory_tasks`: Celery background memory update worker.
- `app.services.memory_service`: compatibility facade and write orchestration.

Critical fixes already applied:

- Enterprise facts such as "你记得报销政策吗？" route to RAG, not memory answer.
- LangGraph returned state is copied back to the outer `AgentGraphState`.
- Conversation summaries use `summary_message_count` to summarize only unprocessed messages.
- `Message.created_at` now has a Python-side timestamp default to avoid same-second ordering bugs.
- DELETE on long-term memory is now soft delete via `deleted` status.
- Durable memory mutations now write `user_memory_events`.
- Candidate and operation editing now lives under `app.memory.editor`.
- Long-term memory recall now writes explainable logs with recall mode, threshold, candidates, selected ids, and routes.
- Long-term memory vector search is optional and best-effort. PostgreSQL remains the source of truth; Qdrant is only an acceleration index.
- Vector recall uses the same similarity threshold as SQL/Python semantic recall, and low-score vector hits are logged as rejected candidates instead of being injected into the prompt.
- Long-term recall no longer loads every active memory before ranking. Profile memories are loaded as a small sticky set; vector hits are resolved by id; semantic fallback uses a bounded recent candidate set while recall logs still record the total active memory count.
- Full-memory recall phrases are guaranteed to route as memory requests even if the LLM classifier says `rag`.
- Manual blank updates fail with a stable HTTP 400 instead of an implementation error.
- `MEMORY_UPDATE_MODE` supports `sync`, `async`, and `disabled`. Async mode first writes a durable `user_memory_update_jobs` row, then dispatches Celery by job id so user-facing latency is not coupled to memory review and broker outages do not erase work.
- Users can list and retry their own async memory update jobs via `/api/memories/update-jobs`.
- Conversation streaming now passes the explicit user `message_id` into the agent state so memory provenance does not depend on a best-effort latest-message lookup.
- Pending memories have explicit approve/reject service and API paths, with dedicated append-only `approve` and `reject` events.
- Users can request a temporary/no-memory turn with phrases such as "without memory", "不要使用记忆", or "临时模式"; that turn skips recall and durable memory updates.
- Automatic memory writes do not rely only on the LLM-provided `sensitivity` field. A deterministic policy guard blocks obvious secrets, tokens, credentials, contact details, and private identifiers before active or pending memories can be created from chat.
- Automatic multi-operation memory edits now run as a single unit of work. If a later operation fails, earlier memory writes from the same review are rolled back instead of leaving the update job partially applied.
- Memory context is formatted under `MEMORY_CONTEXT_MAX_TOKENS` plus `MEMORY_CONTEXT_MAX_CHARS`, splitting budget across long-term memory, conversation summary, and recent dialogue. Within the long-term memory section, sticky preferences and higher-confidence or higher-importance memories are kept ahead of generic memories.
- Users can export memory governance data, restore soft-deleted memories, and permanently purge a single memory plus its vector point. Purge keeps a detached audit snapshot but removes the durable memory row.
- User-driven memory governance actions (`create`, `update`, `approve`, `reject`, `restore`, `delete`, and `purge`) are also mirrored into the global `AuditLog` without storing memory content, source text, or content hashes.
- Recall quality is measurable from logs: mode distribution, route distribution, fallback count, vector count, empty-result rate, below-threshold candidates, average selected count, and top recalled memories.
- Memory reconcile can inspect the optional Qdrant memory index for missing, stale, or unexpected vector points; dry-run reports drift and apply mode repairs it with `vector_sync` / `vector_delete` audit events.

## Optional Enhancements

The core memory architecture is in place. Future product/compliance work can add:

1. Labeled recall-evaluation datasets if offline precision/recall benchmarking becomes necessary.
2. Bulk export/delete UX and retention windows for memory logs if compliance requirements demand it.
3. A richer admin dashboard over memory update jobs and recall-quality metrics.

## Design Rules

- Memory is never enterprise evidence.
- User-owned memory cannot bypass knowledge-base access control.
- Do not create memory by default.
- Prefer update/supersede over duplicate create.
- Prefer pending over active when confidence is not high.
- Prefer soft delete over physical delete.
- Every recalled memory should be explainable by route, score, category, status, and source where that signal is available.
