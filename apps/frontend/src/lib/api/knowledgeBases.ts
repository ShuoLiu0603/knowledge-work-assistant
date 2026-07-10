import { apiRequest } from "./client";
import type { KnowledgeBase, KnowledgeBaseVisibility } from "./types";

export async function listKnowledgeBases(token: string): Promise<KnowledgeBase[]> {
  return apiRequest<KnowledgeBase[]>("/knowledge-bases", { token });
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
      description: payload.description ?? null,
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
  await apiRequest(`/knowledge-bases/${id}`, { method: "DELETE", token });
}
