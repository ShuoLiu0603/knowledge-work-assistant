import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { login, type User } from "../lib/api";
import { isLikelyEmail } from "../lib/auth";
import type { StoredAuth } from "../stores/authStore";

type Props = {
  onAuthenticated: (auth: StoredAuth, user: User) => void;
};

export function LoginPage({ onAuthenticated }: Props) {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (!isLikelyEmail(email)) {
      setError("请输入有效邮箱。");
      return;
    }

    setSubmitting(true);
    try {
      const result = await login({ email, password });
      onAuthenticated(result.auth, result.user);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="shell">
      <section className="panel auth-panel">
        <p className="eyebrow">Agentic RAG</p>
        <h1>登录</h1>

        <form className="form" onSubmit={handleSubmit}>
          <label>
            <span>邮箱</span>
            <input value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" />
          </label>

          <label>
            <span>密码</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
            />
          </label>

          {error && <p className="form-error">{error}</p>}

          <button type="submit" disabled={submitting}>
            {submitting ? "登录中" : "登录"}
          </button>
        </form>

        <p className="switch-link">
          还没有账号？<Link to="/register">注册</Link>
        </p>
      </section>
    </main>
  );
}
