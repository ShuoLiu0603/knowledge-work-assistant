import { clearAuth, getStoredAuth, saveAuth, type StoredAuth } from "../authStore";
import type { StreamHandlers, Conversation, Message, Citation, RetrievalLog, AgentRun } from "./types";

// --- internal helpers ---

const apiBaseUrl = (import.meta as Record<string, any>).env?.VITE_API_BASE_URL ?? "http://localhost:8000/api";
let refreshPromise: Promise<StoredAuth | null> | null = null;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export interface RequestOptions extends RequestInit {
  token?: string;
}

// --- auth helpers ---

function latestAccessToken(fallback?: string): string | undefined {
  return getStoredAuth()?.accessToken ?? fallback;
}

async function refreshStoredAuth(): Promise<StoredAuth | null> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const auth = getStoredAuth();
      if (!auth) return null;
      try {
        const response = await sendApiRequest("/auth/refresh", {
          method: "POST",
          body: JSON.stringify({ refresh_token: auth.refreshToken }),
        });
        const data = await parseApiResponse<{ access_token: string; refresh_token: string }>(response);
        const refreshed: StoredAuth = {
          accessToken: data.access_token,
          refreshToken: data.refresh_token,
        };
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

// --- core request ---

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await sendApiRequest(path, options, options.token ? latestAccessToken(options.token) : undefined);

  if (response.status === 401 && options.token) {
    const refreshed = await refreshStoredAuth();
    if (refreshed) {
      return parseApiResponse(await sendApiRequest(path, options, refreshed.accessToken));
    }
  }

  return parseApiResponse(response);
}

async function sendApiRequest(path: string, options: RequestOptions, token?: string): Promise<Response> {
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
    const msg = await readErrorMessage(response);
    throw new ApiError(msg, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const body: any = await response.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    return response.statusText;
  }
  return response.statusText;
}

// --- SSE streaming ---

export async function streamConversationMessage(
  token: string,
  conversationId: string,
  payload: { question: string; top_k?: number; memory_mode?: "auto" | "normal" | "off" },
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
    const msg = await readErrorMessage(response);
    throw new ApiError(msg, response.status);
  }
  if (!response.body) {
    throw new ApiError("流式响应不可用", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let streamError = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const err = handleSseFrame(frame, handlers);
      if (err) streamError = err;
    }
  }

  if (buffer.trim()) {
    const err = handleSseFrame(buffer, handlers);
    if (err) streamError = err;
  }

  if (streamError) {
    throw new ApiError(streamError, 200);
  }
}

function handleSseFrame(frame: string, handlers: StreamHandlers): string {
  const normalized = frame.replace(/\r/g, "");
  const lines = normalized.split("\n");
  let event = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trimStart());
  }
  if (dataLines.length === 0) return "";

  const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;

  switch (event) {
    case "conversation":
      handlers.onConversation?.(data as unknown as Conversation);
      break;
    case "user_message":
      handlers.onUserMessage?.(data as unknown as Message);
      break;
    case "trace":
      handlers.onTrace?.(data as unknown as { node: string; status: string });
      break;
    case "token":
      handlers.onToken?.(String(data.content ?? data.text ?? ""));
      break;
    case "citations":
      handlers.onCitations?.(((data.citations as Citation[]) ?? []));
      break;
    case "retrieval_log":
      handlers.onRetrievalLog?.(data as unknown as RetrievalLog);
      break;
    case "agent_run":
      handlers.onAgentRun?.(data as unknown as AgentRun);
      break;
    case "assistant_message":
      handlers.onAssistantMessage?.(data as unknown as Message);
      break;
    case "done":
      handlers.onDone?.(data as unknown as { conversation_id: string; message_id: string });
      break;
    case "error": {
      const msg = String(data.message ?? "流式请求失败");
      handlers.onError?.(msg);
      return msg;
    }
  }
  return "";
}
