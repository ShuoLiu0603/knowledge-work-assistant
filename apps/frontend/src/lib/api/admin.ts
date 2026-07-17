import { apiRequest } from "./client";
import type { AdminMetrics, AdminUser } from "./types";

export async function fetchAdminMetrics(token: string): Promise<AdminMetrics> {
  return apiRequest<AdminMetrics>("/admin/metrics", { token });
}

export async function listAdminUsers(token: string): Promise<AdminUser[]> {
  return apiRequest<AdminUser[]>("/admin/users", { token });
}

export async function createAdminUser(
  token: string,
  payload: {
    email: string;
    username: string;
    password: string;
    is_admin: boolean;
    security_level: number;
    department_id?: string | null;
  },
): Promise<AdminUser> {
  return apiRequest<AdminUser>("/admin/users", {
    method: "POST",
    token,
    body: JSON.stringify(payload),
  });
}

export async function updateAdminUser(
  token: string,
  userId: string,
  payload: { security_level?: number; is_active?: boolean; is_admin?: boolean; department_id?: string | null },
): Promise<AdminUser> {
  return apiRequest<AdminUser>(`/admin/users/${userId}`, { method: "PATCH", token, body: JSON.stringify(payload) });
}

export async function deleteAdminUser(token: string, userId: string): Promise<void> {
  await apiRequest(`/admin/users/${userId}`, { method: "DELETE", token });
}
