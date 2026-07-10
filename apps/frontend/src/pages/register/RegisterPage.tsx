import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { register, type User } from "../../lib/api";
import { isLikelyEmail, isStrongEnoughPassword } from "../../lib/auth";
import type { StoredAuth } from "../../lib/authStore";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import styles from "../login/LoginPage.module.css";

type Props = { onAuthenticated: (auth: StoredAuth, user: User) => void };

export function RegisterPage({ onAuthenticated }: Props) {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!isLikelyEmail(email)) return setError("请输入有效邮箱。");
    if (!username.trim()) return setError("请输入用户名。");
    if (!isStrongEnoughPassword(password)) return setError("密码至少 8 位。");
    setSubmitting(true);
    try {
      const r = await register({ email, username, password });
      onAuthenticated(r.auth, r.user);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <div className={styles.card}>
        <p className={styles.eyebrow}>Agentic RAG</p>
        <h1>注册</h1>
        <form onSubmit={handleSubmit}>
          <Input label="邮箱" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          <Input label="用户名" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
          <Input label="密码" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          {error && <p className={styles.error}>{error}</p>}
          <Button type="submit" disabled={submitting} style={{ width: "100%" }}>{submitting ? "注册中" : "注册"}</Button>
        </form>
        <p className={styles.footer}>已有账号？<Link to="/login">登录</Link></p>
      </div>
    </main>
  );
}
