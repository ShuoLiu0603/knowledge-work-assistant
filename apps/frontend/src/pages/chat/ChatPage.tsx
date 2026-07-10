import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  createUserMemory,
  deleteUserMemory,
  createConversation,
  deleteConversation,
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
} from "../../lib/api";

import { AppShell } from "../../components/layout/AppShell";
import { TopBar } from "../../components/layout/TopBar";
import { Loading } from "../../components/ui/Loading";
import { EmptyState } from "../../components/ui/EmptyState";
import { StatusPill } from "../../components/ui/StatusPill";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";

import { SearchScopeSelector } from "./components/SearchScopeSelector";
import { ConversationList } from "./components/ConversationList";
import { MessageList } from "./components/MessageList";
import { ChatInput } from "./components/ChatInput";
import { CitationPanel } from "./components/CitationPanel";
import { MemoryPanel } from "./components/MemoryPanel";
import { AgentTracePanel } from "./components/AgentTracePanel";
import { LlmCallLogPanel } from "./components/LlmCallLogPanel";
import { RetrievalLogPanel } from "./components/RetrievalLogPanel";
import styles from "./ChatPage.module.css";

type Props = { token: string; user: User; onLogout: () => Promise<void> };

const STREAMING_ID = "streaming-assistant";

// --- stateless helpers ---

function createStreamingMessage(convId: string, content: string, citations: Citation[]): Message {
  return {
    id: STREAMING_ID,
    conversation_id: convId,
    role: "assistant",
    content,
    status: "completed",
    memory_enabled: true,
    citations,
    agent_trace: [],
    token_usage: {},
    error_message: null,
    created_at: new Date().toISOString(),
  };
}

function markStopped(msgs: Message[]): Message[] {
  return msgs.map((m) =>
    m.id === STREAMING_ID ? { ...m, status: "failed", error_message: "已停止。" } as Message : m,
  );
}

function withoutStreaming(msgs: Message[]): Message[] {
  return msgs.filter((m) => m.id !== STREAMING_ID);
}

function upsertMsg(msgs: Message[], msg: Message): Message[] {
  return msgs.some((m) => m.id === msg.id) ? msgs.map((m) => (m.id === msg.id ? msg : m)) : [...msgs, msg];
}

function upsertConv(convs: Conversation[], conv: Conversation): Conversation[] {
  return [conv, ...convs.filter((c) => c.id !== conv.id)];
}

function upsertLog(logs: RetrievalLog[], log: RetrievalLog): RetrievalLog[] {
  return [log, ...logs.filter((l) => l.id !== log.id)];
}

function upsertRun(runs: AgentRun[], run: AgentRun): AgentRun[] {
  return [run, ...runs.filter((r) => r.id !== run.id)];
}

function appendStreamingToken(msgs: Message[], convId: string, token: string, citations: Citation[]): Message[] {
  const existing = msgs.find((m) => m.id === STREAMING_ID);
  if (!existing) return [...msgs, createStreamingMessage(convId, token, citations)];
  return msgs.map((m) =>
    m.id === STREAMING_ID
      ? { ...m, content: m.content + token, citations: citations.length > 0 ? citations : m.citations }
      : m,
  );
}

function upsertStreamingCitations(msgs: Message[], convId: string, citations: Citation[]): Message[] {
  const existing = msgs.find((m) => m.id === STREAMING_ID);
  if (!existing) return [...msgs, createStreamingMessage(convId, "", citations)];
  return msgs.map((m) => (m.id === STREAMING_ID ? { ...m, citations } : m));
}

function isAbortError(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

function traceStatus(t: { node: string; status: string }): string {
  if (t.node === "agent_graph") {
    if (t.status === "started") return "编排中";
    if (t.status === "completed") return "生成完成";
    if (t.status === "failed") return "生成失败";
  }
  return `${t.node} ${t.status}`;
}

function convPayload(scope: SearchScope, kbId: string | null, deptId: string | null) {
  if (scope === "single") return { search_scope: scope as SearchScope, knowledge_base_id: kbId };
  if (scope === "department") return { search_scope: scope as SearchScope, department_id: deptId };
  return { search_scope: scope as SearchScope };
}

function targetLabel(scope: SearchScope, kb: KnowledgeBase | null, u: User): string {
  if (scope === "single") return kb?.name ?? "单个知识库";
  if (scope === "department") return u.department_name ? `${u.department_name} 部门知识库` : "部门知识库";
  if (scope === "public") return "公共知识库";
  return "所有知识库";
}

function targetDesc(scope: SearchScope, u: User): string {
  if (scope === "single") return "仅检索所选知识库";
  if (scope === "department") return u.department_name ? `仅检索 ${u.department_name} 部门库` : "未设置部门";
  if (scope === "public") return "仅检索公共知识库";
  return "检索所有可访问知识库";
}

// --- main component ---

export function ChatPage({ token, user, onLogout }: Props) {
  const streamAbort = useRef<AbortController | null>(null);
  const streamingCitationsRef = useRef<Citation[]>([]);

  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selectedKbId, setSelectedKbId] = useState("");
  const [targetScope, setTargetScope] = useState<SearchScope>("single");
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState("");
  const [msgs, setMsgs] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [memoryOff, setMemoryOff] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [streamStatus, setStreamStatus] = useState("就绪");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [streamingCitations, setStreamingCitations] = useState<Citation[]>([]);
  const [selectedCitMsgId, setSelectedCitMsgId] = useState("");
  const [logs, setLogs] = useState<RetrievalLog[]>([]);
  const [selectedLogId, setSelectedLogId] = useState("");
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [memories, setMemories] = useState<UserMemory[]>([]);
  const [newMemory, setNewMemory] = useState("");
  const [llmLogs, setLlmLogs] = useState<LlmCallLog[]>([]);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null); // conversation id to delete
  const [confirmDeleteMem, setConfirmDeleteMem] = useState<string | null>(null); // memory id to delete

  // right panel tabs
  const [rightTab, setRightTab] = useState<"citations" | "memories" | "trace" | "llm" | "retrieval">("citations");

  const personalKbs = useMemo(
    () => kbs.filter((kb) => kb.visibility === "private" && kb.owner_id === user.id),
    [kbs, user.id],
  );
  const selectedKb = useMemo(() => personalKbs.find((k) => k.id === selectedKbId) ?? null, [personalKbs, selectedKbId]);
  const canUse = targetScope !== "single" || Boolean(selectedKb);
  const convFilters = useMemo(
    () =>
      targetScope === "single"
        ? { search_scope: targetScope, knowledge_base_id: selectedKb?.id }
        : { search_scope: targetScope },
    [targetScope, selectedKb?.id],
  );
  const visibleCit =
    selectedCitMsgId === STREAMING_ID ? streamingCitations : (msgs.find((m) => m.id === selectedCitMsgId)?.citations ?? []);
  const showsSL = targetScope !== "single";

  // --- load KBs ---
  useEffect(() => {
    let active = true;
    listKnowledgeBases(token)
      .then((items) => {
        if (!active) return;
        setKbs(items);
        const pItems = items.filter((i) => i.visibility === "private" && i.owner_id === user.id);
        setSelectedKbId((cur) => cur || pItems[0]?.id || "");
        setTargetScope((cur) =>
          cur === "single" && pItems.length === 0 ? (user.department_id ? "department" as SearchScope : "public" as SearchScope) : cur,
        );
        setError("");
      })
      .catch((e) => { if (active) setError(e instanceof Error ? e.message : "知识库加载失败。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [token, user]);

  // --- auto-fix single scope ---
  useEffect(() => {
    if (targetScope === "single" && personalKbs.length > 0 && !selectedKb) {
      setSelectedKbId(personalKbs[0].id);
    }
  }, [targetScope, personalKbs, selectedKb]);

  // --- load memories ---
  useEffect(() => {
    listUserMemories(token, "active").then(setMemories).catch(() => {});
  }, [token]);

  // --- load conversations ---
  useEffect(() => {
    if (!canUse) {
      setConvs([]);
      setActiveConvId("");
      setMsgs([]);
      setLogs([]);
      setSelectedLogId("");
      setRuns([]);
      setSelectedRunId("");
      setLlmLogs([]);
      return;
    }
    let active = true;
    listConversations(token, convFilters)
      .then(async (items) => {
        if (!active) return;
        setConvs(items);
        const first = items[0];
        setActiveConvId(first?.id ?? "");
        setSelectedCitMsgId("");
        setStreamingCitations([]);
        if (first) {
          const [loadedMsgs, loadedLogs, loadedRuns, loadedLlm] = await Promise.all([
            listMessages(token, first.id),
            listRetrievalLogs(token, { conversation_id: first.id }),
            listAgentRuns(token, { conversation_id: first.id }),
            listLlmCallLogs(token, { conversation_id: first.id }),
          ]);
          if (active) {
            setMsgs(loadedMsgs);
            setLogs(loadedLogs);
            setSelectedLogId(loadedLogs[0]?.id ?? "");
            setRuns(loadedRuns);
            setSelectedRunId(loadedRuns[0]?.id ?? "");
            setLlmLogs(loadedLlm);
          }
        }
      })
      .catch((e) => { if (active) setError(e instanceof Error ? e.message : "会话加载失败。"); });
    return () => { active = false; };
  }, [canUse, convFilters, token]);

  // --- handlers ---

  async function selectConv(conv: Conversation) {
    setActiveConvId(conv.id);
    setSelectedCitMsgId("");
    setStreamingCitations([]);
    setError("");
    try {
      const [loadedMsgs, loadedLogs, loadedRuns, loadedLlm] = await Promise.all([
        listMessages(token, conv.id),
        listRetrievalLogs(token, { conversation_id: conv.id }),
        listAgentRuns(token, { conversation_id: conv.id }),
        listLlmCallLogs(token, { conversation_id: conv.id }),
      ]);
      setMsgs(loadedMsgs);
      setLogs(loadedLogs);
      setSelectedLogId(loadedLogs[0]?.id ?? "");
      setRuns(loadedRuns);
      setSelectedRunId(loadedRuns[0]?.id ?? "");
      setLlmLogs(loadedLlm);
    } catch (e) {
      setError(e instanceof Error ? e.message : "消息加载失败。");
    }
  }

  async function newConv() {
    if (!canUse) return;
    setError("");
    try {
      const c = await createConversation(token, convPayload(targetScope, selectedKb?.id ?? null, user.department_id));
      setConvs((prev) => [c, ...prev]);
      setActiveConvId(c.id);
      setMsgs([]);
      setSelectedCitMsgId("");
      setStreamingCitations([]);
      setLogs([]);
      setSelectedLogId("");
      setRuns([]);
      setSelectedRunId("");
      setLlmLogs([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "新建会话失败。");
    }
  }

  async function delConv(convId: string) {
    if (streaming) return;
    setError("");
    try {
      await deleteConversation(token, convId);
      const next = convs.filter((c) => c.id !== convId);
      setConvs(next);
      if (activeConvId !== convId) return;
      if (next[0]) {
        await selectConv(next[0]);
      } else {
        setActiveConvId("");
        setMsgs([]);
        setLogs([]);
        setRuns([]);
        setLlmLogs([]);
        setSelectedCitMsgId("");
        setStreamingCitations([]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除会话失败。");
    }
  }

  function confirmDelConv(convId: string) {
    setConfirmDelete(convId);
  }

  function createMem() {
    const content = newMemory.trim();
    if (!content) return;
    setError("");
    createUserMemory(token, { content, category: "general", kind: "preference" })
      .then((m) => {
        setMemories((prev) => [m, ...prev.filter((x) => x.id !== m.id)]);
        setNewMemory("");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "记忆保存失败。"));
  }

  function delMem(memId: string) {
    setError("");
    deleteUserMemory(token, memId)
      .then(() => setMemories((prev) => prev.filter((m) => m.id !== memId)))
      .catch((e) => setError(e instanceof Error ? e.message : "记忆删除失败。"));
  }

  function confirmDelMem(memId: string) {
    setConfirmDeleteMem(memId);
  }

  async function sendMsg() {
    if (streaming || !canUse) return;
    const trimmed = question.trim();
    if (!trimmed) return setError("请输入问题。");
    const memoryMode = memoryOff ? "off" : "normal";

    setQuestion("");
    setMemoryOff(false);
    setError("");
    setStreaming(true);
    setStreamStatus("准备请求");
    setStreamingCitations([]);
    streamingCitationsRef.current = [];
    const ctrl = new AbortController();
    streamAbort.current = ctrl;

    try {
      const conv = await ensureConv();
      // 立即显示空助手消息，展示思考中动画
      setMsgs((prev) => [...withoutStreaming(prev), createStreamingMessage(conv.id, "", [])]);
      await streamConversationMessage(
        token, conv.id, { question: trimmed, memory_mode: memoryMode },
        {
          onConversation(updated) {
            setConvs((prev) => upsertConv(prev, updated));
            setActiveConvId(updated.id);
          },
          onUserMessage(msg) {
            setMsgs((prev) => [...withoutStreaming(prev), msg, createStreamingMessage(conv.id, "", [])]);
            setStreamStatus("检索上下文");
          },
          onTrace(t) { setStreamStatus(traceStatus(t)); },
          onCitations(cit) {
            streamingCitationsRef.current = cit;
            setStreamingCitations(cit);
            setSelectedCitMsgId(STREAMING_ID);
            setMsgs((prev) => upsertStreamingCitations(prev, conv.id, cit));
          },
          onRetrievalLog(log) {
            setLogs((prev) => upsertLog(prev, log));
            setSelectedLogId(log.id);
            setStreamStatus("检索完成");
          },
          onAgentRun(run) {
            setRuns((prev) => upsertRun(prev, run));
            setSelectedRunId(run.id);
          },
          onToken(tok) {
            setStreamStatus("生成回答");
            setMsgs((prev) => appendStreamingToken(prev, conv.id, tok, streamingCitationsRef.current));
          },
          onAssistantMessage(msg) {
            setMsgs((prev) => upsertMsg(withoutStreaming(prev), msg));
            setSelectedCitMsgId(msg.id);
          },
          onError(msg) { setError(msg); setStreamStatus("生成失败"); },
        },
        ctrl.signal,
      );
      // refresh after stream
      const [uc, ul, ur, ull, um] = await Promise.all([
        listConversations(token, convFilters),
        listRetrievalLogs(token, { conversation_id: conv.id }),
        listAgentRuns(token, { conversation_id: conv.id }),
        listLlmCallLogs(token, { conversation_id: conv.id }),
        listUserMemories(token, "active"),
      ]);
      setConvs(uc);
      setLogs(ul);
      setSelectedLogId(ul[0]?.id ?? selectedLogId);
      setRuns(ur);
      setSelectedRunId(ur[0]?.id ?? selectedRunId);
      setLlmLogs(ull);
      setMemories(um);
    } catch (e) {
      if (isAbortError(e)) {
        setMsgs((prev) => markStopped(prev));
        setStreamStatus("已停止");
        return;
      }
      setError(e instanceof Error ? e.message : "流式问答失败，请重试。");
      setQuestion(trimmed);
      setMemoryOff(memoryMode === "off");
      setStreamStatus("生成失败");
    } finally {
      streamAbort.current = null;
      setStreaming(false);
    }
  }

  function stopStream() { streamAbort.current?.abort(); }

  async function ensureConv(): Promise<Conversation> {
    const existing = convs.find((c) => c.id === activeConvId);
    if (existing) return existing;
    if (!canUse) throw new Error("请先选择可用的检索目标。");
    const c = await createConversation(token, convPayload(targetScope, selectedKb?.id ?? null, user.department_id));
    setConvs((prev) => [c, ...prev]);
    setActiveConvId(c.id);
    return c;
  }

  // --- render ---

  const TABS: { key: typeof rightTab; label: string; count?: number }[] = [
    { key: "citations", label: "引用", count: visibleCit.length },
    { key: "memories", label: "记忆", count: memories.length },
    { key: "trace", label: "Agent", count: runs.length },
    { key: "llm", label: "LLM", count: llmLogs.length },
    { key: "retrieval", label: "检索", count: logs.length },
  ];

  return (
    <AppShell>
      <TopBar user={{ username: user.username, is_admin: user.is_admin }} onLogout={onLogout} />

      <div className={styles.workspace}>
        {/* left sidebar */}
        <aside className={styles.leftRail}>
          <SearchScopeSelector
            targetScope={targetScope}
            onChange={(s) => { setTargetScope(s); setError(""); }}
            personalKbs={personalKbs}
            selectedKbId={selectedKbId}
            onKbChange={(id) => { setSelectedKbId(id); setError(""); }}
            disabled={streaming}
          />
          <ConversationList
            conversations={convs}
            activeId={activeConvId}
            onSelect={selectConv}
            onDelete={confirmDelConv}
            onNew={newConv}
            disabled={streaming}
          />
        </aside>

        {/* center: chat */}
        <main className={styles.center}>
          {(loading || error) && (
            <div className={styles.noticeStack}>
              {loading && <Loading />}
              {error && <p className={styles.errorBanner}>{error}</p>}
            </div>
          )}

          <div className={styles.targetBar}>
            <div>
              <strong className={styles.targetTitle}>{targetLabel(targetScope, selectedKb, user)}</strong>
              <span className={styles.targetMeta}>{targetDesc(targetScope, user)} · 密级 L{user.security_level}</span>
            </div>
            <StatusPill variant={streaming ? "streaming" : "active"} label={streaming ? streamStatus : "就绪"} />
          </div>

          {!loading && kbs.length === 0 && (
            <EmptyState
              title="暂无可访问知识库"
              description={user.is_admin ? "请先创建公开知识库并上传文档。" : "你可以先创建一个私人知识库，或等待管理员发布公开知识库。"}
              action={<Link to="/knowledge-bases">前往知识库</Link>}
            />
          )}

          <MessageList
            messages={msgs}
            streaming={streaming}
            retrievalLogs={logs}
            agentRuns={runs}
            onViewCitations={setSelectedCitMsgId}
            onViewRetrieval={setSelectedLogId}
            onViewAgentTrace={setSelectedRunId}
          />

          <ChatInput
            value={question}
            onChange={setQuestion}
            onSubmit={sendMsg}
            onStop={stopStream}
            streaming={streaming}
            disabled={!canUse}
            memoryOff={memoryOff}
            onMemoryOffChange={setMemoryOff}
          />
        </main>

        {/* right panel */}
        <aside className={styles.rightRail}>
          {/* tab bar */}
          <div className={styles.tabBar}>
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setRightTab(t.key)}
                className={`${styles.tab} ${rightTab === t.key ? styles.tabActive : ""}`}
              >
                {t.label}{t.count != null ? ` (${t.count})` : ""}
              </button>
            ))}
          </div>

          <div className={styles.rightBody}>
            {rightTab === "citations" && <CitationPanel citations={visibleCit} showsSecurityLevel={showsSL} />}
            {rightTab === "memories" && (
              <MemoryPanel memories={memories} newMemory={newMemory} onNewMemoryChange={setNewMemory} onCreate={createMem} onDelete={confirmDelMem} />
            )}
            {rightTab === "trace" && <AgentTracePanel runs={runs} selectedId={selectedRunId} onSelect={setSelectedRunId} />}
            {rightTab === "llm" && <LlmCallLogPanel logs={llmLogs} />}
            {rightTab === "retrieval" && <RetrievalLogPanel logs={logs} selectedId={selectedLogId} onSelect={setSelectedLogId} />}
          </div>
        </aside>
      </div>

      {confirmDelete && (
        <ConfirmDialog
          title="删除会话"
          message="确定要永久删除此会话？所有消息将不可恢复。"
          onConfirm={() => { delConv(confirmDelete); setConfirmDelete(null); }}
          onCancel={() => setConfirmDelete(null)}
        />
      )}
      {confirmDeleteMem && (
        <ConfirmDialog
          title="删除记忆"
          message="确定要永久删除此记忆？此操作不可撤销。"
          onConfirm={() => { delMem(confirmDeleteMem); setConfirmDeleteMem(null); }}
          onCancel={() => setConfirmDeleteMem(null)}
        />
      )}
    </AppShell>
  );
}
