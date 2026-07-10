import { apiRequest } from "./client";
import type { AgentRun, RetrievalLog, LlmCallLog, AskKnowledgeBaseResult, SearchScope } from "./types";

export async function listRetrievalLogs(
  token: string,
  filters: { knowledge_base_id?: string; conversation_id?: string; message_id?: string } = {},
): Promise<RetrievalLog[]> {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v) p.set(k, v);
  }
  const q = p.toString() ? `?${p.toString()}` : "";
  return apiRequest<RetrievalLog[]>(`/retrieval-logs${q}`, { token });
}

export async function listAgentRuns(
  token: string,
  filters: { knowledge_base_id?: string; conversation_id?: string; message_id?: string } = {},
): Promise<AgentRun[]> {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v) p.set(k, v);
  }
  const q = p.toString() ? `?${p.toString()}` : "";
  return apiRequest<AgentRun[]>(`/agent-runs${q}`, { token });
}

export async function createAgentRun(
  token: string,
  payload: { knowledge_base_id?: string | null; input: string; top_k?: number; search_scope?: SearchScope; department_id?: string | null },
): Promise<AgentRun> {
  return apiRequest<AgentRun>("/agent-runs", { method: "POST", token, body: JSON.stringify(payload) });
}

export async function listLlmCallLogs(
  token: string,
  filters: { conversation_id?: string; agent_name?: string; limit?: number } = {},
): Promise<LlmCallLog[]> {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== "") p.set(k, String(v));
  }
  const q = p.toString() ? `?${p.toString()}` : "";
  return apiRequest<LlmCallLog[]>(`/llm-logs${q}`, { token });
}

export async function askKnowledgeBase(
  token: string,
  kbId: string,
  payload: { question: string; top_k?: number; search_scope?: SearchScope; department_id?: string | null },
): Promise<AskKnowledgeBaseResult> {
  return apiRequest<AskKnowledgeBaseResult>(`/knowledge-bases/${kbId}/ask`, {
    method: "POST",
    token,
    body: JSON.stringify(payload),
  });
}
