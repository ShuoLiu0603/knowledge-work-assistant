import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { login, type User } from "../../lib/api";
import { isLikelyEmail } from "../../lib/auth";
import type { StoredAuth } from "../../lib/authStore";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import styles from "./LoginPage.module.css";

type Props = { onAuthenticated: (auth: StoredAuth, user: User) => void };

export function LoginPage({ onAuthenticated }: Props) {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!isLikelyEmail(email)) return setError("请输入有效邮箱。");
    setSubmitting(true);
    try {
      const r = await login({ email, password });
      onAuthenticated(r.auth, r.user);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.card}>
        <p className={styles.eyebrow}>Agentic RAG</p>
        <h1>登录</h1>
        <form onSubmit={handleSubmit}>
          <Input label="邮箱" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          <Input label="密码" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          {error && <p className={styles.error}>{error}</p>}
          <Button type="submit" disabled={submitting} style={{ width: "100%" }}>{submitting ? "登录中" : "登录"}</Button>
        </form>
        <p className={styles.footer}>还没有账号？<Link to="/register">注册</Link></p>
      </div>
    </main>
  );
}
