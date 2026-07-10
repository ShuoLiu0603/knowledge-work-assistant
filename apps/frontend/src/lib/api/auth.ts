import { apiRequest } from "./client";
import type { User } from "./types";
import type { StoredAuth } from "../authStore";

function toStoredAuth(r: { access_token: string; refresh_token: string }): StoredAuth {
  return { accessToken: r.access_token, refreshToken: r.refresh_token };
}

type AuthResponse = {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  user: User;
};

export async function register(payload: {
  email: string;
  username: string;
  password: string;
}): Promise<{ auth: StoredAuth; user: User }> {
  const r = await apiRequest<AuthResponse>("/auth/register", { method: "POST", body: JSON.stringify(payload) });
  return { auth: toStoredAuth(r), user: r.user };
}

export async function login(payload: {
  email: string;
  password: string;
}): Promise<{ auth: StoredAuth; user: User }> {
  const r = await apiRequest<AuthResponse>("/auth/login", { method: "POST", body: JSON.stringify(payload) });
  return { auth: toStoredAuth(r), user: r.user };
}

export async function fetchMe(token: string): Promise<User> {
  return apiRequest<User>("/me", { token });
}

export async function refreshAccessToken(refreshToken: string): Promise<StoredAuth> {
  const r = await apiRequest<AuthResponse>("/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  return toStoredAuth(r);
}

export async function logout(refreshToken: string): Promise<void> {
  await apiRequest("/auth/logout", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
}
