const storageKey = "agentic-rag-auth";

export type StoredAuth = {
  accessToken: string;
  refreshToken: string;
};

export function getStoredAuth(): StoredAuth | null {
  const raw = window.localStorage.getItem(storageKey);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as Partial<StoredAuth>;
    if (parsed.accessToken && parsed.refreshToken) {
      return {
        accessToken: parsed.accessToken,
        refreshToken: parsed.refreshToken,
      };
    }
  } catch {
    clearAuth();
  }

  return null;
}

export function saveAuth(auth: StoredAuth): void {
  window.localStorage.setItem(storageKey, JSON.stringify(auth));
}

export function clearAuth(): void {
  window.localStorage.removeItem(storageKey);
}
