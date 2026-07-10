import { apiRequest } from "./client";
import type { UserMemory, UserMemoryExport, UserMemoryUpdateJob } from "./types";

export async function listUserMemories(token: string, status?: string): Promise<UserMemory[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiRequest<UserMemory[]>(`/memories${q}`, { token });
}

export async function createUserMemory(
  token: string,
  payload: {
    content: string;
    category?: string;
    kind?: string;
    canonical_key?: string;
    memory_layer?: string;
    profile_slot?: string;
    pinned?: boolean;
    confirm_sensitive?: boolean;
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
    expected_revision?: number;
    content?: string;
    status?: string;
    category?: string;
    kind?: string;
    canonical_key?: string;
    memory_layer?: string;
    profile_slot?: string;
    pinned?: boolean;
    confirm_sensitive?: boolean;
  },
): Promise<UserMemory> {
  return apiRequest<UserMemory>(`/memories/${memoryId}`, {
    method: "PATCH",
    token,
    body: JSON.stringify(payload),
  });
}

export async function deleteUserMemory(token: string, memoryId: string): Promise<void> {
  await apiRequest(`/memories/${memoryId}`, { method: "DELETE", token });
}

export async function approveUserMemory(token: string, memoryId: string): Promise<UserMemory> {
  return apiRequest<UserMemory>(`/memories/${memoryId}/approve`, { method: "POST", token });
}

export async function rejectUserMemory(token: string, memoryId: string): Promise<UserMemory> {
  return apiRequest<UserMemory>(`/memories/${memoryId}/reject`, { method: "POST", token });
}

export async function restoreUserMemory(token: string, memoryId: string): Promise<UserMemory> {
  return apiRequest<UserMemory>(`/memories/${memoryId}/restore`, { method: "POST", token });
}

export async function purgeUserMemory(token: string, memoryId: string): Promise<void> {
  await apiRequest(`/memories/${memoryId}/purge`, { method: "DELETE", token });
}

export async function exportUserMemoryData(token: string): Promise<UserMemoryExport> {
  return apiRequest<UserMemoryExport>("/memories/export", { token });
}

export async function listUserMemoryUpdateJobs(token: string, status?: string): Promise<UserMemoryUpdateJob[]> {
  const params = new URLSearchParams({ limit: "50" });
  if (status) params.set("status", status);
  return apiRequest<UserMemoryUpdateJob[]>(`/memories/update-jobs?${params.toString()}`, { token });
}

export async function retryUserMemoryUpdateJob(token: string, jobId: string): Promise<UserMemoryUpdateJob> {
  return apiRequest<UserMemoryUpdateJob>(`/memories/update-jobs/${jobId}/retry`, {
    method: "POST",
    token,
  });
}
