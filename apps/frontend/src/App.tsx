import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { fetchMe, logout, refreshAccessToken, type User } from "./lib/api";
import { getStoredAuth, saveAuth, clearAuth, type StoredAuth } from "./lib/authStore";
import { LoginPage } from "./pages/login/LoginPage";
import { RegisterPage } from "./pages/register/RegisterPage";
import { ChatPage } from "./pages/chat/ChatPage";
import { KnowledgeBaseListPage } from "./pages/knowledge-bases/KnowledgeBaseListPage";
import { KnowledgeBaseDetailPage } from "./pages/knowledge-bases/KnowledgeBaseDetailPage";
import { MemoriesPage } from "./pages/memories/MemoriesPage";
import { AdminMetricsPage } from "./pages/admin/AdminMetricsPage";

type AuthState = { auth: StoredAuth | null; user: User | null; loading: boolean };

export function App() {
  const [state, setState] = useState<AuthState>({ auth: getStoredAuth(), user: null, loading: true });

  useEffect(() => {
    let active = true;
    async function load() {
      const auth = getStoredAuth();
      if (!auth) { if (active) setState({ auth: null, user: null, loading: false }); return; }
      try {
        const user = await fetchMe(auth.accessToken);
        if (active) setState({ auth, user, loading: false });
      } catch {
        try {
          const fresh = await refreshAccessToken(auth.refreshToken);
          saveAuth(fresh);
          const user = await fetchMe(fresh.accessToken);
          if (active) setState({ auth: fresh, user, loading: false });
        } catch {
          clearAuth();
          if (active) setState({ auth: null, user: null, loading: false });
        }
      }
    }
    void load();
    return () => { active = false; };
  }, []);

  function handleAuth(auth: StoredAuth, user: User) {
    saveAuth(auth);
    setState({ auth, user, loading: false });
  }

  async function handleLogout() {
    if (state.auth) await logout(state.auth.refreshToken).catch(() => {});
    clearAuth();
    setState({ auth: null, user: null, loading: false });
  }

  if (state.loading) {
    return (
      <main style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
        <div style={{ textAlign: "center" }}>
          <p style={{ color: "var(--color-primary)", fontWeight: 600, fontSize: 12, textTransform: "uppercase", letterSpacing: 0.5, margin: "0 0 4px" }}>Agentic RAG</p>
          <h1 style={{ margin: 0, fontSize: 18, color: "var(--color-text)" }}>正在检查登录状态...</h1>
        </div>
      </main>
    );
  }

  const { auth, user } = state;

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage onAuthenticated={handleAuth} />} />
        <Route path="/register" element={<RegisterPage onAuthenticated={handleAuth} />} />
        <Route path="/" element={auth && user ? <Navigate to="/chat" replace /> : <Navigate to="/login" replace />} />
        <Route path="/chat" element={auth && user ? <ChatPage token={auth.accessToken} user={user} onLogout={handleLogout} /> : <Navigate to="/login" replace />} />
        <Route path="/knowledge-bases" element={auth && user ? <KnowledgeBaseListPage token={auth.accessToken} user={user} onLogout={handleLogout} /> : <Navigate to="/login" replace />} />
        <Route path="/knowledge-bases/:id" element={auth && user ? <KnowledgeBaseDetailPage token={auth.accessToken} user={user} onLogout={handleLogout} /> : <Navigate to="/login" replace />} />
        <Route path="/memories" element={auth && user ? <MemoriesPage token={auth.accessToken} user={user} onLogout={handleLogout} /> : <Navigate to="/login" replace />} />
        <Route path="/admin" element={auth && user?.is_admin ? <AdminMetricsPage token={auth.accessToken} user={user} onLogout={handleLogout} /> : auth && user ? <Navigate to="/chat" replace /> : <Navigate to="/login" replace />} />
        <Route path="/metrics" element={auth && user ? <Navigate to={user.is_admin ? "/admin" : "/chat"} replace /> : <Navigate to="/login" replace />} />
        <Route path="*" element={<Navigate to={user ? "/" : "/login"} replace />} />
      </Routes>
    </BrowserRouter>
  );
}
