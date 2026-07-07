import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { register, type User } from "../lib/api";
import { isLikelyEmail, isStrongEnoughPassword } from "../lib/auth";
import type { StoredAuth } from "../stores/authStore";

type Props = {
  onAuthenticated: (auth: StoredAuth, user: User) => void;
};

export function RegisterPage({ onAuthenticated }: Props) {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
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

    if (!username.trim()) {
      setError("请输入用户名。");
      return;
    }

    if (!isStrongEnoughPassword(password)) {
      setError("密码至少 8 位。");
      return;
    }

    setSubmitting(true);
    try {
      const result = await register({ email, username, password });
      onAuthenticated(result.auth, result.user);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="shell">
      <section className="panel auth-panel">
        <p className="eyebrow">Agentic RAG</p>
        <h1>注册</h1>

        <form className="form" onSubmit={handleSubmit}>
          <label>
            <span>邮箱</span>
            <input value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" />
          </label>

          <label>
            <span>用户名</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
          </label>

          <label>
            <span>密码</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="new-password"
            />
          </label>

          {error && <p className="form-error">{error}</p>}

          <button type="submit" disabled={submitting}>
            {submitting ? "注册中" : "注册"}
          </button>
        </form>

        <p className="switch-link">
          已有账号？<Link to="/login">登录</Link>
        </p>
      </section>
    </main>
  );
}
