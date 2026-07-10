import { apiRequest } from "./client";
import type { Department } from "./types";

export async function listDepartments(token: string): Promise<Department[]> {
  return apiRequest<Department[]>("/departments", { token });
}

export async function createDepartment(
  token: string,
  payload: { name: string; description?: string | null },
): Promise<Department> {
  return apiRequest<Department>("/departments", {
    method: "POST",
    token,
    body: JSON.stringify({ name: payload.name, description: payload.description ?? null }),
  });
}
