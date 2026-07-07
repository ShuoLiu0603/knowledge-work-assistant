import { clearAuth, getStoredAuth, saveAuth, type StoredAuth } from "../stores/authStore";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";
let refreshPromise: Promise<StoredAuth | null> | null = null;

export type User = {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  is_admin: boolean;
  security_level: number;
  department_id: string | null;
  department_name: string | null;
};

export type KnowledgeBaseVisibility = "private" | "department" | "public";
export type SearchScope = "single" | "department" | "public" | "accessible";

export type Department = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
};

export type KnowledgeBase = {
  id: string;
  owner_id: string;
  department_id: string | null;
  department_name: string | null;
  name: string;
  description: string | null;
  visibility: KnowledgeBaseVisibility;
  role: "owner" | "editor" | "viewer";
  created_at: string;
  updated_at: string;
};

export type DocumentItem = {
  id: string;
  knowledge_base_id: string;
  uploader_id: string | null;
  file_name: string;
  file_ext: string;
  mime_type: string | null;
  file_size: number;
  status: "uploaded" | "parsing" | "chunking" | "embedding" | "indexed" | "failed";
  error_message: string | null;
  chunk_count: number;
  security_level: number;
  content_hash: string;
  created_at: string;
  updated_at: string;
};

export type DocumentChunk = {
  id: string;
  document_id: string;
  knowledge_base_id: string;
  chunk_index: number;
  content: string;
  qdrant_point_id: string | null;
  token_count: number;
  title_path: string | null;
  page_number: number | null;
  section_name: string | null;
  security_level: number;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type DocumentUploadResult = {
  document_id: string;
  status: string;
  job_id: string;
  security_level: number;
};

export type Citation = {
  chunk_id: string;
  document_id: string;
  knowledge_base_id: string;
  file_name: string;
  chunk_index: number;
  score: number;
  content_preview: string;
  title_path: string | null;
  page_number: number | null;
  section_name: string | null;
  security_level: number;
  rrf_score: number | null;
  retrieval_routes: string[];
};

export type RetrievalLog = {
  id: string;
  user_id: string;
  knowledge_base_id: string | null;
  scope_type: SearchScope;
  searched_knowledge_base_ids: string[];
  conversation_id: string | null;
  message_id: string | null;
  question: string;
  rewritten_query: string;
  sub_questions: unknown[];
  expanded_queries: unknown[];
  retrieval_routes: unknown[];
  candidates: unknown[];
  selected_chunks: unknown[];
  rrf_k: number;
  reranker_enabled: boolean;
  compression_chars_saved: number;
  created_at: string;
};

export type AgentTraceStep = {
  node: string;
  action: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
};

export type AgentRun = {
  id: string;
  user_id: string;
  knowledge_base_id: string | null;
  conversation_id: string | null;
  message_id: string | null;
  retrieval_log_id: string | null;
  input: string;
  intent: "rag" | "memory" | "chat" | "summary" | "writing";
  status: "running" | "completed" | "failed";
  answer: string;
  citations: Citation[];
  trace: AgentTraceStep[];
  state: Record<string, unknown>;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type UserMemory = {
  id: string;
  user_id: string;
  content: string;
  content_hash: string;
  status: "active" | "pending" | "superseded" | "ignored";
  kind: string;
  category: string;
  source_text: string;
  merge_count: number;
  touched_count: number;
  superseded_by_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  last_touched_at: string;
};

export type Feedback = {
  id: string;
  user_id: string;
  message_id: string;
  rating: 1 | -1;
  reason: string | null;
  created_at: string;
};

export type LlmCallLog = {
  id: string;
  user_id: string | null;
  conversation_id: string | null;
  agent_name: string | null;
  provider: string;
  model_name: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number | null;
  latency_ms: number | null;
  status: string;
  fallback_used: boolean;
  error_message: string | null;
  created_at: string;
};

export type AdminMetrics = {
  generated_at: string;
  scope: string;
  conversation_count: number;
  message_count: number;
  retrieval_log_count: number;
  llm_call_count: number;
  total_tokens: number;
  average_llm_latency_ms: number | null;
  fallback_call_count: number;
  feedback_count: number;
  positive_feedback_count: number;
  negative_feedback_count: number;
  positive_feedback_rate: number | null;
  average_selected_chunks: number | null;
  recent_llm_errors: Record<string, unknown>[];
};

export type AdminUser = {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  is_admin: boolean;
  security_level: number;
  department_id: string | null;
  department_name: string | null;
  created_at: string;
};

export type AuditLog = {
  id: string;
  actor_user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  outcome: string;
  security_level: number | null;
  detail: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type AskKnowledgeBaseResult = {
  question: string;
  answer: string;
  citations: Citation[];
  retrieval_log: RetrievalLog | null;
};

export type Conversation = {
  id: string;
  knowledge_base_id: string | null;
  knowledge_base_name: string | null;
  search_scope: SearchScope;
  search_department_id: string | null;
  target_label: string;
  title: string;
  summary: string | null;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  status: "completed" | "failed";
  citations: Citation[];
  agent_trace: unknown[];
  token_usage: Record<string, unknown>;
  error_message: string | null;
  created_at: string;
};

export type StreamHandlers = {
  onConversation?: (conversation: Conversation) => void;
  onUserMessage?: (message: Message) => void;
  onTrace?: (trace: { node: string; status: string }) => void;
  onToken?: (token: string) => void;
  onCitations?: (citations: Citation[]) => void;
  onRetrievalLog?: (log: RetrievalLog) => void;
  onAgentRun?: (run: AgentRun) => void;
  onAssistantMessage?: (message: Message) => void;
  onDone?: (payload: { conversation_id: string; message_id: string }) => void;
  onError?: (message: string) => void;
};

type AuthResponse = {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  user: User;
};

type RequestOptions = RequestInit & {
  token?: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export async function register(payload: {
  email: string;
  username: string;
  password: string;
}): Promise<{ auth: StoredAuth; user: User }> {
  const response = await apiRequest<AuthResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return toAuthResult(response);
}

export async function login(payload: {
  email: string;
  password: string;
}): Promise<{ auth: StoredAuth; user: User }> {
  const response = await apiRequest<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return toAuthResult(response);
}

export async function fetchMe(token: string): Promise<User> {
  return apiRequest<User>("/me", { token });
}

export async function refreshAccessToken(refreshToken: string): Promise<StoredAuth> {
  const response = await apiRequest<AuthResponse>("/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  return toStoredAuth(response);
}

export async function logout(refreshToken: string): Promise<void> {
  await apiRequest("/auth/logout", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
}

export async function listKnowledgeBases(token: string): Promise<KnowledgeBase[]> {
  return apiRequest<KnowledgeBase[]>("/knowledge-bases", { token });
}

export async function listDepartments(token: string): Promise<Department[]> {
  return apiRequest<Department[]>("/departments", { token });
}

export async function createDepartment(
  token: string,
  payload: {
    name: string;
    description?: string | null;
  },
): Promise<Department> {
  return apiRequest<Department>("/departments", {
    method: "POST",
    token,
    body: JSON.stringify({
      name: payload.name,
      description: payload.description || null,
    }),
  });
}

export async function createKnowledgeBase(
  token: string,
  payload: {
    name: string;
    description?: string;
    visibility?: KnowledgeBaseVisibility;
    department_id?: string | null;
  },
): Promise<KnowledgeBase> {
  return apiRequest<KnowledgeBase>("/knowledge-bases", {
    method: "POST",
    token,
    body: JSON.stringify({
      name: payload.name,
      description: payload.description || null,
      visibility: payload.visibility ?? "private",
      department_id: payload.department_id ?? null,
    }),
  });
}

export async function getKnowledgeBase(token: string, id: string): Promise<KnowledgeBase> {
  return apiRequest<KnowledgeBase>(`/knowledge-bases/${id}`, { token });
}

export async function updateKnowledgeBase(
  token: string,
  id: string,
  payload: {
    name?: string;
    description?: string | null;
    visibility?: KnowledgeBaseVisibility;
    department_id?: string | null;
  },
): Promise<KnowledgeBase> {
  return apiRequest<KnowledgeBase>(`/knowledge-bases/${id}`, {
    method: "PATCH",
    token,
    body: JSON.stringify(payload),
  });
}

export async function deleteKnowledgeBase(token: string, id: string): Promise<void> {
  await apiRequest(`/knowledge-bases/${id}`, {
    method: "DELETE",
    token,
  });
}

export async function listDocuments(token: string, kbId: string): Promise<DocumentItem[]> {
  return apiRequest<DocumentItem[]>(`/knowledge-bases/${kbId}/documents`, { token });
}

export async function uploadDocument(
  token: string,
  kbId: string,
  file: File,
  securityLevel = 1,
): Promise<DocumentUploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("security_level", String(securityLevel));
  return apiRequest<DocumentUploadResult>(`/knowledge-bases/${kbId}/documents`, {
    method: "POST",
    token,
    body: formData,
  });
}

export async function deleteDocument(token: string, documentId: string): Promise<void> {
  await apiRequest(`/documents/${documentId}`, {
    method: "DELETE",
    token,
  });
}

export async function listDocumentChunks(token: string, documentId: string): Promise<DocumentChunk[]> {
  return apiRequest<DocumentChunk[]>(`/documents/${documentId}/chunks`, { token });
}

export async function askKnowledgeBase(
  token: string,
  kbId: string,
  payload: {
    question: string;
    top_k?: number;
    search_scope?: SearchScope;
    department_id?: string | null;
  },
): Promise<AskKnowledgeBaseResult> {
  return apiRequest<AskKnowledgeBaseResult>(`/knowledge-bases/${kbId}/ask`, {
    method: "POST",
    token,
    body: JSON.stringify(payload),
  });
}

export async function listConversations(
  token: string,
  filters: {
    knowledge_base_id?: string;
    search_scope?: SearchScope;
  } = {},
): Promise<Conversation[]> {
  const params = new URLSearchParams();
  if (filters.knowledge_base_id) {
    params.set("knowledge_base_id", filters.knowledge_base_id);
  }
  if (filters.search_scope) {
    params.set("search_scope", filters.search_scope);
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiRequest<Conversation[]>(`/conversations${query}`, { token });
}

export async function createConversation(
  token: string,
  payload: {
    knowledge_base_id?: string | null;
    search_scope: SearchScope;
    department_id?: string | null;
    title?: string;
  },
): Promise<Conversation> {
  return apiRequest<Conversation>("/conversations", {
    method: "POST",
    token,
    body: JSON.stringify(payload),
  });
}

export async function listMessages(token: string, conversationId: string): Promise<Message[]> {
  return apiRequest<Message[]>(`/conversations/${conversationId}/messages`, { token });
}

export async function deleteConversation(token: string, conversationId: string): Promise<void> {
  await apiRequest(`/conversations/${conversationId}`, {
    method: "DELETE",
    token,
  });
}

export async function listRetrievalLogs(
  token: string,
  filters: {
    knowledge_base_id?: string;
    conversation_id?: string;
    message_id?: string;
  } = {},
): Promise<RetrievalLog[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiRequest<RetrievalLog[]>(`/retrieval-logs${query}`, { token });
}

export async function listAgentRuns(
  token: string,
  filters: {
    knowledge_base_id?: string;
    conversation_id?: string;
    message_id?: string;
  } = {},
): Promise<AgentRun[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiRequest<AgentRun[]>(`/agent-runs${query}`, { token });
}

export async function createAgentRun(
  token: string,
  payload: {
    knowledge_base_id?: string | null;
    input: string;
    top_k?: number;
    search_scope?: SearchScope;
    department_id?: string | null;
  },
): Promise<AgentRun> {
  return apiRequest<AgentRun>("/agent-runs", {
    method: "POST",
    token,
    body: JSON.stringify(payload),
  });
}

export async function listUserMemories(token: string, status?: string): Promise<UserMemory[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiRequest<UserMemory[]>(`/memories${query}`, { token });
}

export async function createUserMemory(
  token: string,
  payload: {
    content: string;
    category?: string;
    kind?: string;
  },
): Promise<UserMemory> {
  return apiRequest<UserMemory>("/memories", {
    method: "POST",
    token,
    body: JSON.stringify(payload),
  });
}

export async function updateUserMemory(
  token: string,
  memoryId: string,
  payload: {
    content?: string;
    status?: string;
    category?: string;
    kind?: string;
  },
): Promise<UserMemory> {
  return apiRequest<UserMemory>(`/memories/${memoryId}`, {
    method: "PATCH",
    token,
    body: JSON.stringify(payload),
  });
}

export async function deleteUserMemory(token: string, memoryId: string): Promise<void> {
  await apiRequest(`/memories/${memoryId}`, {
    method: "DELETE",
    token,
  });
}

export async function listLlmCallLogs(
  token: string,
  filters: {
    conversation_id?: string;
    agent_name?: string;
    limit?: number;
  } = {},
): Promise<LlmCallLog[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiRequest<LlmCallLog[]>(`/llm-logs${query}`, { token });
}

export async function createFeedback(
  token: string,
  payload: {
    message_id: string;
    rating: 1 | -1;
    reason?: string | null;
  },
): Promise<Feedback> {
  return apiRequest<Feedback>("/feedbacks", {
    method: "POST",
    token,
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminMetrics(token: string): Promise<AdminMetrics> {
  return apiRequest<AdminMetrics>("/admin/metrics", { token });
}

export async function listAdminUsers(token: string): Promise<AdminUser[]> {
  return apiRequest<AdminUser[]>("/admin/users", { token });
}

export async function listAuditLogs(token: string, limit = 50): Promise<AuditLog[]> {
  return apiRequest<AuditLog[]>(`/admin/audit-logs?limit=${encodeURIComponent(String(limit))}`, { token });
}

export async function updateAdminUser(
  token: string,
  userId: string,
  payload: {
    security_level?: number;
    is_active?: boolean;
    is_admin?: boolean;
    department_id?: string | null;
  },
): Promise<AdminUser> {
  return apiRequest<AdminUser>(`/admin/users/${userId}`, {
    method: "PATCH",
    token,
    body: JSON.stringify(payload),
  });
}

export async function streamConversationMessage(
  token: string,
  conversationId: string,
  payload: {
    question: string;
    top_k?: number;
  },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let accessToken = latestAccessToken(token);
  let response = await fetch(`${apiBaseUrl}/conversations/${conversationId}/messages/stream`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (response.status === 401) {
    const refreshed = await refreshStoredAuth();
    if (refreshed) {
      accessToken = refreshed.accessToken;
      response = await fetch(`${apiBaseUrl}/conversations/${conversationId}/messages/stream`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        signal,
      });
    }
  }

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new ApiError(message, response.status);
  }
  if (!response.body) {
    throw new ApiError("Streaming response is not available", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let streamError = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const error = handleSseFrame(frame, handlers);
      if (error) {
        streamError = error;
      }
    }
  }

  if (buffer.trim()) {
    const error = handleSseFrame(buffer, handlers);
    if (error) {
      streamError = error;
    }
  }

  if (streamError) {
    throw new ApiError(streamError, 200);
  }
}

async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await sendApiRequest(path, options, options.token ? latestAccessToken(options.token) : undefined);

  if (response.status === 401 && options.token) {
    const refreshed = await refreshStoredAuth();
    if (refreshed) {
      return parseApiResponse(await sendApiRequest(path, options, refreshed.accessToken));
    }
  }

  return parseApiResponse(response);
}

async function sendApiRequest<T>(path: string, options: RequestOptions, token?: string): Promise<Response> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  return fetch(`${apiBaseUrl}${path}`, {
    ...options,
    headers,
  });
}

async function parseApiResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

function latestAccessToken(fallback?: string): string | undefined {
  return getStoredAuth()?.accessToken || fallback;
}

async function refreshStoredAuth(): Promise<StoredAuth | null> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const auth = getStoredAuth();
      if (!auth) {
        return null;
      }
      try {
        const refreshed = await refreshAccessToken(auth.refreshToken);
        saveAuth(refreshed);
        return refreshed;
      } catch {
        clearAuth();
        return null;
      } finally {
        refreshPromise = null;
      }
    })();
  }
  return refreshPromise;
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    return response.statusText;
  }
  return response.statusText;
}

function toAuthResult(response: AuthResponse): { auth: StoredAuth; user: User } {
  return {
    auth: toStoredAuth(response),
    user: response.user,
  };
}

function toStoredAuth(response: AuthResponse): StoredAuth {
  return {
    accessToken: response.access_token,
    refreshToken: response.refresh_token,
  };
}

function handleSseFrame(frame: string, handlers: StreamHandlers): string {
  const normalized = frame.replace(/\r/g, "");
  const lines = normalized.split("\n");
  let event = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return "";
  }

  const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
  if (event === "conversation") {
    handlers.onConversation?.(data as Conversation);
  } else if (event === "user_message") {
    handlers.onUserMessage?.(data as Message);
  } else if (event === "trace") {
    handlers.onTrace?.(data as { node: string; status: string });
  } else if (event === "token") {
    handlers.onToken?.(String(data.content ?? data.text ?? ""));
  } else if (event === "citations") {
    handlers.onCitations?.((data.citations as Citation[]) ?? []);
  } else if (event === "retrieval_log") {
    handlers.onRetrievalLog?.(data as RetrievalLog);
  } else if (event === "agent_run") {
    handlers.onAgentRun?.(data as AgentRun);
  } else if (event === "assistant_message") {
    handlers.onAssistantMessage?.(data as Message);
  } else if (event === "done") {
    handlers.onDone?.(data as { conversation_id: string; message_id: string });
  } else if (event === "error") {
    const message = String(data.message ?? "Streaming request failed");
    handlers.onError?.(message);
    return message;
  }
  return "";
}
