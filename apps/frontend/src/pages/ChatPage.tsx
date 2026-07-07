import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  createUserMemory,
  createFeedback,
  createConversation,
  deleteConversation,
  deleteUserMemory,
  listAgentRuns,
  listConversations,
  listKnowledgeBases,
  listLlmCallLogs,
  listMessages,
  listRetrievalLogs,
  listUserMemories,
  streamConversationMessage,
  type AgentRun,
  type Citation,
  type Conversation,
  type KnowledgeBase,
  type LlmCallLog,
  type Message,
  type RetrievalLog,
  type SearchScope,
  type UserMemory,
  type User,
} from "../lib/api";

type Props = {
  token: string;
  user: User;
  onLogout: () => Promise<void>;
};

const STREAMING_MESSAGE_ID = "streaming-assistant";

export function ChatPage({ token, user, onLogout }: Props) {
  const navigate = useNavigate();
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const streamingCitationsRef = useRef<Citation[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKbId, setSelectedKbId] = useState("");
  const [targetScope, setTargetScope] = useState<SearchScope>("single");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamStatus, setStreamStatus] = useState("就绪");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedCitationMessageId, setSelectedCitationMessageId] = useState("");
  const [streamingCitations, setStreamingCitations] = useState<Citation[]>([]);
  const [retrievalLogs, setRetrievalLogs] = useState<RetrievalLog[]>([]);
  const [selectedRetrievalLogId, setSelectedRetrievalLogId] = useState("");
  const [agentRuns, setAgentRuns] = useState<AgentRun[]>([]);
  const [selectedAgentRunId, setSelectedAgentRunId] = useState("");
  const [memories, setMemories] = useState<UserMemory[]>([]);
  const [newMemory, setNewMemory] = useState("");
  const [llmLogs, setLlmLogs] = useState<LlmCallLog[]>([]);

  const personalKnowledgeBases = useMemo(
    () => knowledgeBases.filter((item) => item.visibility === "private" && item.owner_id === user.id),
    [knowledgeBases, user.id],
  );
  const selectedKnowledgeBase = useMemo(
    () => personalKnowledgeBases.find((item) => item.id === selectedKbId) ?? null,
    [personalKnowledgeBases, selectedKbId],
  );
  const canUseTarget = targetScope !== "single" || Boolean(selectedKnowledgeBase);
  const conversationFilters = useMemo(
    () =>
      targetScope === "single"
        ? { search_scope: targetScope, knowledge_base_id: selectedKnowledgeBase?.id }
        : { search_scope: targetScope },
    [targetScope, selectedKnowledgeBase?.id],
  );
  const selectedCitationMessage = messages.find((message) => message.id === selectedCitationMessageId);
  const visibleCitations =
    selectedCitationMessageId === STREAMING_MESSAGE_ID ? streamingCitations : (selectedCitationMessage?.citations ?? []);
  const activeRetrievalLog =
    retrievalLogs.find((log) => log.id === selectedRetrievalLogId) ?? retrievalLogs[0] ?? null;
  const activeAgentRun = agentRuns.find((run) => run.id === selectedAgentRunId) ?? agentRuns[0] ?? null;
  const showsSecurityLevel = targetScope !== "single";

  useEffect(() => {
    let active = true;
    listKnowledgeBases(token)
      .then((items) => {
        if (!active) {
          return;
        }
        setKnowledgeBases(items);
        const personalItems = items.filter((item) => item.visibility === "private" && item.owner_id === user.id);
        setSelectedKbId((current) => current || personalItems[0]?.id || "");
        setTargetScope((current) => (current === "single" && personalItems.length === 0 ? defaultTargetScope(user) : current));
        setError("");
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "知识库加载失败。");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [token, user]);

  useEffect(() => {
    let active = true;
    listUserMemories(token)
      .then((items) => {
        if (active) {
          setMemories(items);
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [token]);

  useEffect(() => {
    if (targetScope === "single" && personalKnowledgeBases.length > 0 && !selectedKnowledgeBase) {
      setSelectedKbId(personalKnowledgeBases[0].id);
    }
  }, [targetScope, personalKnowledgeBases, selectedKnowledgeBase]);

  useEffect(() => {
    if (!canUseTarget) {
      setConversations([]);
      setActiveConversationId("");
      setMessages([]);
      setRetrievalLogs([]);
      setSelectedRetrievalLogId("");
      setAgentRuns([]);
      setSelectedAgentRunId("");
      setLlmLogs([]);
      return;
    }

    let active = true;
    listConversations(token, conversationFilters)
      .then(async (items) => {
        if (!active) {
          return;
        }
        setConversations(items);
        const first = items[0];
        setActiveConversationId(first?.id || "");
        setSelectedCitationMessageId("");
        setStreamingCitations([]);
        if (first) {
          const [loadedMessages, loadedLogs, loadedRuns, loadedLlmLogs] = await Promise.all([
            listMessages(token, first.id),
            listRetrievalLogs(token, { conversation_id: first.id }),
            listAgentRuns(token, { conversation_id: first.id }),
            listLlmCallLogs(token, { conversation_id: first.id }),
          ]);
          if (active) {
            setMessages(loadedMessages);
            setRetrievalLogs(loadedLogs);
            setSelectedRetrievalLogId(loadedLogs[0]?.id || "");
            setAgentRuns(loadedRuns);
            setSelectedAgentRunId(loadedRuns[0]?.id || "");
            setLlmLogs(loadedLlmLogs);
          }
        } else {
          setMessages([]);
          setRetrievalLogs([]);
          setSelectedRetrievalLogId("");
          setAgentRuns([]);
          setSelectedAgentRunId("");
          setLlmLogs([]);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "会话加载失败。");
        }
      });
    return () => {
      active = false;
    };
  }, [canUseTarget, conversationFilters, token]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  async function handleSelectConversation(conversation: Conversation) {
    setActiveConversationId(conversation.id);
    setSelectedCitationMessageId("");
    setStreamingCitations([]);
    setError("");
    try {
      const [loadedMessages, loadedLogs, loadedRuns, loadedLlmLogs] = await Promise.all([
        listMessages(token, conversation.id),
        listRetrievalLogs(token, { conversation_id: conversation.id }),
        listAgentRuns(token, { conversation_id: conversation.id }),
        listLlmCallLogs(token, { conversation_id: conversation.id }),
      ]);
      setMessages(loadedMessages);
      setRetrievalLogs(loadedLogs);
      setSelectedRetrievalLogId(loadedLogs[0]?.id || "");
      setAgentRuns(loadedRuns);
      setSelectedAgentRunId(loadedRuns[0]?.id || "");
      setLlmLogs(loadedLlmLogs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "消息加载失败。");
    }
  }

  async function handleNewConversation() {
    if (!canUseTarget) {
      return;
    }
    setError("");
    try {
      const created = await createConversation(token, {
        ...conversationCreatePayload(targetScope, selectedKnowledgeBase?.id ?? null, user.department_id),
      });
      setConversations((current) => [created, ...current]);
      setActiveConversationId(created.id);
      setMessages([]);
      setSelectedCitationMessageId("");
      setStreamingCitations([]);
      setRetrievalLogs([]);
      setSelectedRetrievalLogId("");
      setAgentRuns([]);
      setSelectedAgentRunId("");
      setLlmLogs([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "新建会话失败。");
    }
  }

  async function handleDeleteConversation(conversationId: string) {
    if (streaming) {
      return;
    }
    setError("");
    try {
      await deleteConversation(token, conversationId);
      const nextConversations = conversations.filter((item) => item.id !== conversationId);
      setConversations(nextConversations);
      if (activeConversationId !== conversationId) {
        return;
      }
      const next = nextConversations[0];
      if (next) {
        await handleSelectConversation(next);
      } else {
        setActiveConversationId("");
        setMessages([]);
        setRetrievalLogs([]);
        setAgentRuns([]);
        setLlmLogs([]);
        setSelectedCitationMessageId("");
        setStreamingCitations([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除会话失败。");
    }
  }

  async function handleCreateMemory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = newMemory.trim();
    if (!content) {
      return;
    }
    setError("");
    try {
      const created = await createUserMemory(token, {
        content,
        category: "general",
        kind: "preference",
      });
      setMemories((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setNewMemory("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "记忆保存失败。");
    }
  }

  async function handleDeleteMemory(memoryId: string) {
    setError("");
    try {
      await deleteUserMemory(token, memoryId);
      setMemories((current) => current.filter((item) => item.id !== memoryId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "记忆删除失败。");
    }
  }

  async function handleRateMessage(message: Message, rating: 1 | -1) {
    const reason =
      rating < 0
        ? window.prompt("这次回答哪里不对？可以留空。")?.trim() || null
        : null;
    setError("");
    try {
      await createFeedback(token, {
        message_id: message.id,
        rating,
        reason,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "反馈提交失败。");
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (streaming || !canUseTarget) {
      return;
    }

    const trimmed = question.trim();
    if (!trimmed) {
      setError("请输入问题。");
      return;
    }

    setQuestion("");
    setError("");
    setStreaming(true);
    setStreamStatus("准备请求");
    setStreamingCitations([]);
    streamingCitationsRef.current = [];
    const controller = new AbortController();
    streamAbortRef.current = controller;

    try {
      const conversation = await ensureConversation();
      await streamConversationMessage(
        token,
        conversation.id,
        {
          question: trimmed,
        },
        {
          onConversation: (updated) => {
            setConversations((current) => upsertConversation(current, updated));
            setActiveConversationId(updated.id);
          },
          onUserMessage: (message) => {
            setMessages((current) => [...withoutStreamingMessage(current), message]);
            setStreamStatus("检索上下文");
          },
          onTrace: (trace) => {
            setStreamStatus(formatTraceStatus(trace));
          },
          onCitations: (citations) => {
            streamingCitationsRef.current = citations;
            setStreamingCitations(citations);
            setSelectedCitationMessageId(STREAMING_MESSAGE_ID);
            setMessages((current) => upsertStreamingCitations(current, conversation.id, citations));
          },
          onRetrievalLog: (log) => {
            setRetrievalLogs((current) => upsertRetrievalLog(current, log));
            setSelectedRetrievalLogId(log.id);
            setStreamStatus("检索完成");
          },
          onAgentRun: (run) => {
            setAgentRuns((current) => upsertAgentRun(current, run));
            setSelectedAgentRunId(run.id);
          },
          onToken: (tokenChunk) => {
            setStreamStatus("生成回答");
            setMessages((current) =>
              appendStreamingToken(current, conversation.id, tokenChunk, streamingCitationsRef.current),
            );
          },
          onAssistantMessage: (message) => {
            setMessages((current) => upsertMessage(withoutStreamingMessage(current), message));
            setSelectedCitationMessageId(message.id);
          },
          onError: (message) => {
            setError(message);
            setStreamStatus("生成失败");
          },
        },
        controller.signal,
      );
      const updatedConversations = await listConversations(token, conversationFilters);
      setConversations(updatedConversations);
      const updatedLogs = await listRetrievalLogs(token, { conversation_id: conversation.id });
      const updatedRuns = await listAgentRuns(token, { conversation_id: conversation.id });
      const updatedLlmLogs = await listLlmCallLogs(token, { conversation_id: conversation.id });
      const updatedMemories = await listUserMemories(token);
      setRetrievalLogs(updatedLogs);
      setSelectedRetrievalLogId(updatedLogs[0]?.id || selectedRetrievalLogId);
      setAgentRuns(updatedRuns);
      setSelectedAgentRunId(updatedRuns[0]?.id || selectedAgentRunId);
      setLlmLogs(updatedLlmLogs);
      setMemories(updatedMemories);
    } catch (err) {
      if (isAbortError(err)) {
        setMessages((current) => markStreamingStopped(current));
        setStreamStatus("已停止");
        return;
      }
      setError(err instanceof Error ? err.message : "流式问答失败，请重试。");
      setQuestion(trimmed);
      setStreamStatus("生成失败");
    } finally {
      streamAbortRef.current = null;
      setStreaming(false);
    }
  }

  function handleStopStreaming() {
    streamAbortRef.current?.abort();
  }

  async function ensureConversation(): Promise<Conversation> {
    const active = conversations.find((item) => item.id === activeConversationId);
    if (active) {
      return active;
    }
    if (!canUseTarget) {
      throw new Error("请先选择可用的检索目标。");
    }
    const created = await createConversation(token, {
      ...conversationCreatePayload(targetScope, selectedKnowledgeBase?.id ?? null, user.department_id),
    });
    setConversations((current) => [created, ...current]);
    setActiveConversationId(created.id);
    return created;
  }

  async function submitLogout() {
    await onLogout();
    navigate("/login", { replace: true });
  }

  return (
    <main className="app-shell chat-shell">
      <header className="topbar chat-topbar">
        <div>
          <p className="eyebrow">Agentic RAG</p>
          <h1>企业知识问答</h1>
        </div>
        <div className="topbar-actions">
          <Link to="/knowledge-bases">知识库</Link>
          <Link to="/memories">记忆</Link>
          {user.is_admin && <Link to="/admin">管理员控制台</Link>}
          <span>{user.username}</span>
          <button type="button" className="secondary-button" onClick={submitLogout}>
            退出
          </button>
        </div>
      </header>

      <section className="chat-layout">
        <aside className="chat-sidebar">
          <label>
            <span>检索目标</span>
            <select
              value={targetScope}
              onChange={(event) => {
                setTargetScope(event.target.value as SearchScope);
                setError("");
              }}
              disabled={streaming}
            >
              <option value="single" disabled={personalKnowledgeBases.length === 0}>
                单个知识库
              </option>
              <option value="department" disabled={!user.department_id}>
                部门知识库
              </option>
              <option value="public">公共知识库</option>
              <option value="accessible">所有知识库</option>
            </select>
          </label>

          {targetScope === "single" && (
            <label>
              <span>个人知识库</span>
              <select
                value={selectedKbId}
                onChange={(event) => {
                  setSelectedKbId(event.target.value);
                  setError("");
                }}
                disabled={streaming || personalKnowledgeBases.length === 0}
              >
                {personalKnowledgeBases.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            </label>
          )}

          <button type="button" onClick={() => void handleNewConversation()} disabled={!canUseTarget || streaming}>
            新会话
          </button>

          <div className="conversation-list">
            {conversations.map((conversation) => (
              <article
                className={conversation.id === activeConversationId ? "conversation-item active" : "conversation-item"}
                key={conversation.id}
              >
                <button
                  type="button"
                  className="conversation-select"
                  onClick={() => void handleSelectConversation(conversation)}
                  disabled={streaming}
                >
                  <strong>{conversation.title}</strong>
                  <span>{conversation.target_label}</span>
                  <span>{new Date(conversation.updated_at).toLocaleString()}</span>
                </button>
                <button
                  type="button"
                  className="secondary-button conversation-delete"
                  onClick={() => void handleDeleteConversation(conversation.id)}
                  disabled={streaming}
                >
                  删除
                </button>
              </article>
            ))}
          </div>
        </aside>

        <section className="chat-main">
          {loading && <p className="muted">正在加载。</p>}
          {error && <p className="form-error">{error}</p>}

          <div className="chat-status-row">
            <div>
              <strong>{targetLabel(targetScope, selectedKnowledgeBase, user)}</strong>
              <span>
                {targetDescription(targetScope, user)} · 密级 L{user.security_level}
              </span>
            </div>
            <span className={streaming ? "status-pill streaming" : "status-pill active"}>
              {streaming ? streamStatus : "就绪"}
            </span>
          </div>

          {!loading && knowledgeBases.length === 0 && (
            <div className="empty-state">
              <h3>暂无可访问知识库</h3>
              <p>{user.is_admin ? "请先创建公开知识库并上传文档。" : "你可以先创建一个私人知识库，也可以等待管理员发布公开知识库。"}</p>
              <Link to="/knowledge-bases">前往知识库</Link>
            </div>
          )}

          <div className="message-list">
            {messages.map((message) => (
              <article className={`message-bubble ${message.role}`} key={message.id}>
                <div className="message-meta">
                  <strong>{message.role === "user" ? "你" : "助手"}</strong>
                  <span>{message.status}</span>
                </div>
                {message.error_message ? <p className="inline-error">{message.error_message}</p> : <p>{message.content}</p>}
                {message.role === "assistant" && message.citations.length > 0 && (
                  <div className="message-actions">
                    <button
                      type="button"
                      className="secondary-button compact-button"
                      onClick={() => setSelectedCitationMessageId(message.id)}
                    >
                      引用 {message.citations.length}
                    </button>
                    {retrievalLogs.some((log) => log.message_id === message.id) && (
                      <button
                        type="button"
                        className="secondary-button compact-button"
                        onClick={() =>
                          setSelectedRetrievalLogId(
                            retrievalLogs.find((log) => log.message_id === message.id)?.id || "",
                          )
                        }
                      >
                        检索解释
                      </button>
                    )}
                    {agentRuns.some((run) => run.message_id === message.id) && (
                      <button
                        type="button"
                        className="secondary-button compact-button"
                        onClick={() =>
                          setSelectedAgentRunId(agentRuns.find((run) => run.message_id === message.id)?.id || "")
                        }
                      >
                        Agent trace
                      </button>
                    )}
                    <button
                      type="button"
                      className="secondary-button compact-button"
                      onClick={() => void navigator.clipboard?.writeText(message.content)}
                    >
                      复制
                    </button>
                    <button
                      type="button"
                      className="secondary-button compact-button"
                      onClick={() => void handleRateMessage(message, 1)}
                    >
                      点赞
                    </button>
                    <button
                      type="button"
                      className="secondary-button compact-button"
                      onClick={() => void handleRateMessage(message, -1)}
                    >
                      点踩
                    </button>
                  </div>
                )}
              </article>
            ))}
            <div ref={bottomRef} />
          </div>

          <form className="chat-input" onSubmit={(event) => void handleSubmit(event)}>
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={3}
              disabled={streaming || !canUseTarget}
              placeholder="输入问题"
            />
            <div className="chat-input-actions">
              {streaming && (
                <button type="button" className="secondary-button" onClick={handleStopStreaming}>
                  停止
                </button>
              )}
              <button type="submit" disabled={streaming || !canUseTarget}>
                {streaming ? "生成中" : "发送"}
              </button>
            </div>
          </form>
        </section>

        <aside className="citation-panel">
          <div className="section-heading">
            <h2>引用</h2>
            <span>{visibleCitations.length}</span>
          </div>

          {visibleCitations.length === 0 && <p className="muted">暂无引用。</p>}
          <div className="citation-list">
            {visibleCitations.map((citation, index) => (
              <article className="citation-item" key={`${citation.chunk_id}-${index}`}>
                <strong>
                  [{index + 1}] {citation.file_name}
                </strong>
                <p>{citation.content_preview}</p>
                <small>
                  chunk #{citation.chunk_index} · score: {citation.score}
                  {citation.knowledge_base_id ? ` · kb ${shortId(citation.knowledge_base_id)}` : ""}
                  {citation.rrf_score ? ` · rrf ${citation.rrf_score}` : ""}
                  {showsSecurityLevel ? ` · L${citation.security_level}` : ""}
                  {citation.page_number ? ` · page ${citation.page_number}` : ""}
                  {citation.title_path ? ` · ${citation.title_path}` : ""}
                </small>
              </article>
            ))}
          </div>

          <div className="retrieval-panel">
            <div className="section-heading">
              <h2>长期记忆</h2>
              <span>{memories.length}</span>
            </div>

            <form className="memory-form" onSubmit={(event) => void handleCreateMemory(event)}>
              <textarea
                value={newMemory}
                onChange={(event) => setNewMemory(event.target.value)}
                rows={2}
                placeholder="手动添加偏好或背景"
              />
              <button type="submit" disabled={!newMemory.trim()}>
                添加
              </button>
            </form>

            {memories.length === 0 && <p className="muted">暂无长期记忆。</p>}
            <div className="memory-list">
              {memories.slice(0, 8).map((memory) => (
                <article className={`memory-item ${memory.status}`} key={memory.id}>
                  <strong>{memory.category}</strong>
                  <p>{memory.content}</p>
                  <small>
                    {memory.status} · touched {memory.touched_count} · merged {memory.merge_count}
                  </small>
                  <button
                    type="button"
                    className="secondary-button compact-button"
                    onClick={() => void handleDeleteMemory(memory.id)}
                  >
                    删除
                  </button>
                </article>
              ))}
            </div>
          </div>

          <div className="retrieval-panel">
            <div className="section-heading">
              <h2>Agent trace</h2>
              <span>{agentRuns.length}</span>
            </div>

            {agentRuns.length > 1 && (
              <select value={selectedAgentRunId} onChange={(event) => setSelectedAgentRunId(event.target.value)}>
                {agentRuns.map((run) => (
                  <option value={run.id} key={run.id}>
                    {new Date(run.created_at).toLocaleString()} · {run.intent}
                  </option>
                ))}
              </select>
            )}

            {!activeAgentRun && <p className="muted">暂无 Agent trace。</p>}
            {activeAgentRun && (
              <div className="agent-detail">
                <dl className="retrieval-grid">
                  <div>
                    <dt>intent</dt>
                    <dd>{activeAgentRun.intent}</dd>
                  </div>
                  <div>
                    <dt>status</dt>
                    <dd>{activeAgentRun.status}</dd>
                  </div>
                  <div>
                    <dt>retrieval_log_id</dt>
                    <dd>{activeAgentRun.retrieval_log_id || "-"}</dd>
                  </div>
                </dl>
                <div className="trace-list">
                  {activeAgentRun.trace.map((step, index) => (
                    <article className="trace-item" key={`${step.node}-${index}`}>
                      <strong>
                        {index + 1}. {step.node}
                      </strong>
                      <p>{step.action}</p>
                      <small>output: {JSON.stringify(step.output)}</small>
                    </article>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="retrieval-panel">
            <div className="section-heading">
              <h2>LLM 日志</h2>
              <span>{llmLogs.length}</span>
            </div>

            {llmLogs.length === 0 && <p className="muted">暂无 LLM 调用记录。</p>}
            <div className="trace-list">
              {llmLogs.slice(0, 6).map((log) => (
                <article className={`trace-item ${log.status}`} key={log.id}>
                  <strong>
                    {log.agent_name || "unknown"} · {log.status}
                  </strong>
                  <p>
                    {log.provider}/{log.model_name}
                  </p>
                  <small>
                    tokens {log.total_tokens} · latency {log.latency_ms ?? "-"} ms
                    {log.fallback_used ? " · fallback" : ""}
                    {log.error_message ? ` · ${log.error_message}` : ""}
                  </small>
                </article>
              ))}
            </div>
          </div>

          <div className="retrieval-panel">
            <div className="section-heading">
              <h2>检索解释</h2>
              <span>{retrievalLogs.length}</span>
            </div>

            {retrievalLogs.length > 1 && (
              <select value={selectedRetrievalLogId} onChange={(event) => setSelectedRetrievalLogId(event.target.value)}>
                {retrievalLogs.map((log) => (
                  <option value={log.id} key={log.id}>
                    {new Date(log.created_at).toLocaleString()} · {log.rewritten_query}
                  </option>
                ))}
              </select>
            )}

            {!activeRetrievalLog && <p className="muted">暂无检索日志。</p>}
            {activeRetrievalLog && (
              <div className="retrieval-detail">
                <dl className="retrieval-grid">
                  <div>
                    <dt>scope</dt>
                    <dd>{scopeLabel(activeRetrievalLog.scope_type)}</dd>
                  </div>
                  <div>
                    <dt>searched_kbs</dt>
                    <dd>{activeRetrievalLog.searched_knowledge_base_ids.length}</dd>
                  </div>
                  <div>
                    <dt>rewritten_query</dt>
                    <dd>{activeRetrievalLog.rewritten_query}</dd>
                  </div>
                  <div>
                    <dt>retrieval_routes</dt>
                    <dd>{formatList(activeRetrievalLog.retrieval_routes)}</dd>
                  </div>
                  <div>
                    <dt>candidates</dt>
                    <dd>{activeRetrievalLog.candidates.length}</dd>
                  </div>
                  <div>
                    <dt>selected_chunks</dt>
                    <dd>{activeRetrievalLog.selected_chunks.length}</dd>
                  </div>
                  <div>
                    <dt>rrf_k</dt>
                    <dd>{activeRetrievalLog.rrf_k}</dd>
                  </div>
                  <div>
                    <dt>compressed</dt>
                    <dd>{activeRetrievalLog.compression_chars_saved} chars</dd>
                  </div>
                </dl>

                <section>
                  <h3>sub_questions</h3>
                  <p>{formatList(activeRetrievalLog.sub_questions)}</p>
                </section>

                <section>
                  <h3>retrieval_queries</h3>
                  <p>{formatList(activeRetrievalLog.expanded_queries)}</p>
                </section>

                <section>
                  <h3>selected_chunks</h3>
                  <div className="selected-chunk-list">
                    {activeRetrievalLog.selected_chunks.slice(0, 5).map((chunk, index) => (
                      <article className="selected-chunk-item" key={index}>
                        <strong>
                          #{valueFromRecord(chunk, "chunk_index")} · {valueFromRecord(chunk, "file_name")}
                        </strong>
                        <p>{valueFromRecord(chunk, "content_preview")}</p>
                        <small>
                          routes: {formatList(valueListFromRecord(chunk, "retrieval_routes"))}
                          {valueFromRecord(chunk, "knowledge_base_id") ? ` · kb ${shortId(valueFromRecord(chunk, "knowledge_base_id"))}` : ""}
                          {valueFromRecord(chunk, "rrf_score") ? ` · rrf ${valueFromRecord(chunk, "rrf_score")}` : ""}
                        </small>
                      </article>
                    ))}
                  </div>
                </section>
              </div>
            )}
          </div>
        </aside>
      </section>
    </main>
  );
}

function createStreamingMessage(conversationId: string, content: string, citations: Citation[]): Message {
  return {
    id: STREAMING_MESSAGE_ID,
    conversation_id: conversationId,
    role: "assistant",
    content,
    status: "completed",
    citations,
    agent_trace: [],
    token_usage: {},
    error_message: null,
    created_at: new Date().toISOString(),
  };
}

function markStreamingStopped(messages: Message[]): Message[] {
  return messages.map((message) =>
    message.id === STREAMING_MESSAGE_ID
      ? {
          ...message,
          status: "failed",
          error_message: "生成已停止。",
        }
      : message,
  );
}

function formatTraceStatus(trace: { node: string; status: string }): string {
  if (trace.node === "agent_graph" && trace.status === "started") {
    return "编排中";
  }
  if (trace.node === "agent_graph" && trace.status === "completed") {
    return "生成完成";
  }
  if (trace.node === "agent_graph" && trace.status === "failed") {
    return "生成失败";
  }
  return `${trace.node} ${trace.status}`;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function appendStreamingToken(
  messages: Message[],
  conversationId: string,
  token: string,
  citations: Citation[],
): Message[] {
  const existing = messages.find((message) => message.id === STREAMING_MESSAGE_ID);
  if (!existing) {
    return [...messages, createStreamingMessage(conversationId, token, citations)];
  }
  return messages.map((message) =>
    message.id === STREAMING_MESSAGE_ID
      ? {
          ...message,
          content: message.content + token,
          citations: citations.length > 0 ? citations : message.citations,
        }
      : message,
  );
}

function withoutStreamingMessage(messages: Message[]): Message[] {
  return messages.filter((message) => message.id !== STREAMING_MESSAGE_ID);
}

function upsertMessage(messages: Message[], message: Message): Message[] {
  if (messages.some((item) => item.id === message.id)) {
    return messages.map((item) => (item.id === message.id ? message : item));
  }
  return [...messages, message];
}

function upsertConversation(conversations: Conversation[], conversation: Conversation): Conversation[] {
  const next = conversations.filter((item) => item.id !== conversation.id);
  return [conversation, ...next];
}

function upsertStreamingCitations(messages: Message[], conversationId: string, citations: Citation[]): Message[] {
  const existing = messages.find((message) => message.id === STREAMING_MESSAGE_ID);
  if (!existing) {
    return [...messages, createStreamingMessage(conversationId, "", citations)];
  }
  return messages.map((message) =>
    message.id === STREAMING_MESSAGE_ID
      ? {
          ...message,
          citations,
        }
      : message,
  );
}

function upsertRetrievalLog(logs: RetrievalLog[], log: RetrievalLog): RetrievalLog[] {
  const next = logs.filter((item) => item.id !== log.id);
  return [log, ...next];
}

function upsertAgentRun(runs: AgentRun[], run: AgentRun): AgentRun[] {
  const next = runs.filter((item) => item.id !== run.id);
  return [run, ...next];
}

function formatList(values: unknown[]): string {
  if (!values || values.length === 0) {
    return "-";
  }
  return values.map((value) => String(value)).join(", ");
}

function defaultTargetScope(user: User): SearchScope {
  return user.department_id ? "department" : "public";
}

function conversationCreatePayload(
  scope: SearchScope,
  knowledgeBaseId: string | null,
  departmentId: string | null,
): {
  knowledge_base_id?: string | null;
  search_scope: SearchScope;
  department_id?: string | null;
} {
  if (scope === "single") {
    return { search_scope: scope, knowledge_base_id: knowledgeBaseId };
  }
  if (scope === "department") {
    return { search_scope: scope, department_id: departmentId };
  }
  return { search_scope: scope };
}

function targetLabel(scope: SearchScope, knowledgeBase: KnowledgeBase | null, user: User): string {
  if (scope === "single") {
    return knowledgeBase?.name || "单个知识库";
  }
  if (scope === "department") {
    return user.department_name ? `${user.department_name} 部门知识库` : "部门知识库";
  }
  if (scope === "public") {
    return "公共知识库";
  }
  return "所有知识库";
}

function targetDescription(scope: SearchScope, user: User): string {
  if (scope === "single") {
    return "仅检索所选个人知识库";
  }
  if (scope === "department") {
    return user.department_name ? `仅检索 ${user.department_name} 部门库` : "未设置部门";
  }
  if (scope === "public") {
    return "仅检索公共知识库";
  }
  return "检索所有可访问知识库";
}

function scopeLabel(scope: SearchScope | string): string {
  if (scope === "department") {
    return "部门知识库";
  }
  if (scope === "public") {
    return "公共知识库";
  }
  if (scope === "accessible") {
    return "所有知识库";
  }
  return "单个知识库";
}

function shortId(value: string): string {
  return value.length > 8 ? value.slice(0, 8) : value;
}

function valueFromRecord(value: unknown, key: string): string {
  if (!value || typeof value !== "object") {
    return "";
  }
  const record = value as Record<string, unknown>;
  const item = record[key];
  if (item === null || item === undefined) {
    return "";
  }
  return String(item);
}

function valueListFromRecord(value: unknown, key: string): unknown[] {
  if (!value || typeof value !== "object") {
    return [];
  }
  const item = (value as Record<string, unknown>)[key];
  return Array.isArray(item) ? item : [];
}
