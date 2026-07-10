import { apiRequest, streamConversationMessage } from "./client";
import type { Conversation, Message, SearchScope, StreamHandlers } from "./types";

export { streamConversationMessage };
export type { StreamHandlers };

export async function listConversations(
  token: string,
  filters: { knowledge_base_id?: string; search_scope?: SearchScope } = {},
): Promise<Conversation[]> {
  const p = new URLSearchParams();
  if (filters.knowledge_base_id) p.set("knowledge_base_id", filters.knowledge_base_id);
  if (filters.search_scope) p.set("search_scope", filters.search_scope);
  const q = p.toString() ? `?${p.toString()}` : "";
  return apiRequest<Conversation[]>(`/conversations${q}`, { token });
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
  await apiRequest(`/conversations/${conversationId}`, { method: "DELETE", token });
}
