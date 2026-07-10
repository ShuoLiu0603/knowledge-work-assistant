import { apiRequest } from "./client";
import type { AdminMetrics, AdminUser, AuditLog, ExternalCleanupJob, RetentionRun } from "./types";

export async function fetchAdminMetrics(token: string): Promise<AdminMetrics> {
  return apiRequest<AdminMetrics>("/admin/metrics", { token });
}

export async function listAdminUsers(token: string): Promise<AdminUser[]> {
  return apiRequest<AdminUser[]>("/admin/users", { token });
}

export async function listAuditLogs(token: string, limit = 50): Promise<AuditLog[]> {
  return apiRequest<AuditLog[]>(`/admin/audit-logs?limit=${encodeURIComponent(String(limit))}`, { token });
}

export async function listExternalCleanupJobs(
  token: string,
  filters: { status?: string; resource_type?: string; limit?: number } = {},
): Promise<ExternalCleanupJob[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.resource_type) params.set("resource_type", filters.resource_type);
  params.set("limit", String(filters.limit ?? 100));
  return apiRequest<ExternalCleanupJob[]>(`/admin/external-cleanup-jobs?${params.toString()}`, { token });
}

export async function retryExternalCleanupJob(token: string, jobId: string): Promise<ExternalCleanupJob> {
  return apiRequest<ExternalCleanupJob>(`/admin/external-cleanup-jobs/${jobId}/retry`, { method: "POST", token });
}

export async function runOperationalRetention(token: string, dryRun = true): Promise<RetentionRun> {
  return apiRequest<RetentionRun>(`/admin/retention/run?dry_run=${dryRun ? "true" : "false"}`, {
    method: "POST",
    token,
  });
}

export async function updateAdminUser(
  token: string,
  userId: string,
  payload: { security_level?: number; is_active?: boolean; is_admin?: boolean; department_id?: string | null },
): Promise<AdminUser> {
  return apiRequest<AdminUser>(`/admin/users/${userId}`, { method: "PATCH", token, body: JSON.stringify(payload) });
}
