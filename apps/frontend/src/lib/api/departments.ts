import { apiRequest } from "./client";
import type { Department } from "./types";

export async function listDepartments(token: string): Promise<Department[]> {
  return apiRequest<Department[]>("/departments", { token });
}

export async function createDepartment(
  token: string,
  payload: { name: string; description?: string | null; admin_user_id: string },
): Promise<Department> {
  return apiRequest<Department>("/departments", {
    method: "POST",
    token,
    body: JSON.stringify({
      name: payload.name,
      description: payload.description ?? null,
      admin_user_id: payload.admin_user_id,
    }),
  });
}

export async function updateDepartmentAdmin(
  token: string,
  departmentId: string,
  adminUserId: string,
): Promise<Department> {
  return apiRequest<Department>(`/departments/${departmentId}/admin`, {
    method: "PATCH",
    token,
    body: JSON.stringify({ admin_user_id: adminUserId }),
  });
}

export async function deleteDepartment(token: string, departmentId: string): Promise<void> {
  await apiRequest(`/departments/${departmentId}`, { method: "DELETE", token });
}
