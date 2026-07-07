import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { fetchMe, logout as logoutRequest, refreshAccessToken, type User } from "./lib/api";
import { getStoredAuth, saveAuth, clearAuth, type StoredAuth } from "./stores/authStore";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { KnowledgeBaseDetailPage } from "./pages/KnowledgeBaseDetailPage";
import { KnowledgeBaseListPage } from "./pages/KnowledgeBaseListPage";
import { ChatPage } from "./pages/ChatPage";
import { AdminMetricsPage } from "./pages/AdminMetricsPage";
import { MemoriesPage } from "./pages/MemoriesPage";

type AuthState = {
  auth: StoredAuth | null;
  user: User | null;
  loading: boolean;
};

export function App() {
  const [state, setState] = useState<AuthState>({
    auth: getStoredAuth(),
    user: null,
    loading: true,
  });

  useEffect(() => {
    let active = true;

    async function loadUser() {
      const auth = getStoredAuth();
      if (!auth) {
        if (active) {
          setState({ auth: null, user: null, loading: false });
        }
        return;
      }

      try {
        const user = await fetchMe(auth.accessToken);
        if (active) {
          setState({ auth, user, loading: false });
        }
      } catch {
        try {
          const refreshed = await refreshAccessToken(auth.refreshToken);
          saveAuth(refreshed);
          const user = await fetchMe(refreshed.accessToken);
          if (active) {
            setState({ auth: refreshed, user, loading: false });
          }
        } catch {
          clearAuth();
          if (active) {
            setState({ auth: null, user: null, loading: false });
          }
        }
      }
    }

    void loadUser();
    return () => {
      active = false;
    };
  }, []);

  function handleAuth(auth: StoredAuth, user: User) {
    saveAuth(auth);
    setState({ auth, user, loading: false });
  }

  async function handleLogout() {
    if (state.auth) {
      await logoutRequest(state.auth.refreshToken).catch(() => undefined);
    }
    clearAuth();
    setState({ auth: null, user: null, loading: false });
  }

  if (state.loading) {
    return (
      <main className="shell">
        <section className="panel compact">
          <p className="eyebrow">Agentic RAG</p>
          <h1>正在检查登录状态</h1>
        </section>
      </main>
    );
  }

  const auth = state.auth;
  const user = state.user;

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage onAuthenticated={handleAuth} />} />
        <Route path="/register" element={<RegisterPage onAuthenticated={handleAuth} />} />
        <Route
          path="/"
          element={auth && user ? <Navigate to="/chat" replace /> : <Navigate to="/login" replace />}
        />
        <Route
          path="/knowledge-bases"
          element={
            auth && user ? (
              <KnowledgeBaseListPage token={auth.accessToken} user={user} onLogout={handleLogout} />
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
        <Route
          path="/knowledge-bases/:id"
          element={
            auth && user ? (
              <KnowledgeBaseDetailPage token={auth.accessToken} user={user} />
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
        <Route
          path="/chat"
          element={
            auth && user ? (
              <ChatPage token={auth.accessToken} user={user} onLogout={handleLogout} />
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
        <Route
          path="/admin"
          element={
            auth && user && user.is_admin ? (
              <AdminMetricsPage token={auth.accessToken} user={user} onLogout={handleLogout} />
            ) : auth && user ? (
              <Navigate to="/chat" replace />
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
        <Route
          path="/metrics"
          element={
            auth && user ? (
              <Navigate to={user.is_admin ? "/admin" : "/chat"} replace />
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
        <Route
          path="/memories"
          element={
            auth && user ? (
              <MemoriesPage token={auth.accessToken} user={user} onLogout={handleLogout} />
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
        <Route path="*" element={<Navigate to={user ? "/" : "/login"} replace />} />
      </Routes>
    </BrowserRouter>
  );
}
