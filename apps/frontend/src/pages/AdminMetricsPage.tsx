import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  createDepartment,
  fetchAdminMetrics,
  listAuditLogs,
  listAdminUsers,
  listDepartments,
  listKnowledgeBases,
  updateAdminUser,
  type AdminMetrics,
  type AdminUser,
  type AuditLog,
  type Department,
  type KnowledgeBase,
  type User,
} from "../lib/api";

type Props = {
  token: string;
  user: User;
  onLogout: () => Promise<void>;
};

export function AdminMetricsPage({ token, user, onLogout }: Props) {
  const navigate = useNavigate();
  const [metrics, setMetrics] = useState<AdminMetrics | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [departmentName, setDepartmentName] = useState("");
  const [departmentDescription, setDepartmentDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [usersLoading, setUsersLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([
      fetchAdminMetrics(token),
      listAdminUsers(token),
      listAuditLogs(token, 50),
      listKnowledgeBases(token),
      listDepartments(token),
    ])
      .then(([data, loadedUsers, loadedAuditLogs, loadedKnowledgeBases, loadedDepartments]) => {
        if (active) {
          setMetrics(data);
          setUsers(loadedUsers);
          setAuditLogs(loadedAuditLogs);
          setKnowledgeBases(loadedKnowledgeBases);
          setDepartments(loadedDepartments);
          setError("");
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "管理员控制台加载失败。");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [token]);

  async function handleSecurityLevelChange(target: AdminUser, securityLevel: number) {
    setUsersLoading(true);
    setError("");
    try {
      const updated = await updateAdminUser(token, target.id, { security_level: securityLevel });
      setUsers((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "用户清级更新失败。");
    } finally {
      setUsersLoading(false);
    }
  }

  async function handleDepartmentChange(target: AdminUser, departmentId: string) {
    setUsersLoading(true);
    setError("");
    try {
      const updated = await updateAdminUser(token, target.id, {
        department_id: departmentId || null,
      });
      setUsers((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "用户部门更新失败。");
    } finally {
      setUsersLoading(false);
    }
  }

  async function handleCreateDepartment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = departmentName.trim();
    if (!name) {
      return;
    }
    setUsersLoading(true);
    setError("");
    try {
      const created = await createDepartment(token, {
        name,
        description: departmentDescription.trim() || null,
      });
      setDepartments((current) => [...current, created].sort((left, right) => left.name.localeCompare(right.name)));
      setDepartmentName("");
      setDepartmentDescription("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "部门创建失败。");
    } finally {
      setUsersLoading(false);
    }
  }

  async function submitLogout() {
    await onLogout();
    navigate("/login", { replace: true });
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Admin Console</p>
          <h1>管理员控制台</h1>
        </div>
        <div className="topbar-actions">
          <Link to="/chat">问答</Link>
          <Link to="/memories">记忆</Link>
          <span>{user.username}</span>
          <button type="button" className="secondary-button" onClick={submitLogout}>
            退出
          </button>
        </div>
      </header>

      <section className="detail-panel admin-console">
        {loading && <p className="muted">正在加载管理员控制台。</p>}
        {error && <p className="form-error">{error}</p>}

        {metrics && (
          <>
            <section className="admin-summary">
              <div>
                <p className="eyebrow">Security Boundary</p>
                <h2>数据治理与检索安全</h2>
                <p>公开知识库由管理员治理并按等级过滤；私人知识库默认仅 owner 可见，按成员权限隔离。</p>
              </div>
              <dl className="admin-summary-stats">
                <div>
                  <dt>我的清级</dt>
                  <dd>L{user.security_level}</dd>
                </div>
                <div>
                  <dt>数据源</dt>
                  <dd>{knowledgeBases.length}</dd>
                </div>
                <div>
                  <dt>部门</dt>
                  <dd>{departments.length}</dd>
                </div>
                <div>
                  <dt>用户</dt>
                  <dd>{users.length}</dd>
                </div>
              </dl>
            </section>

            <div className="section-heading">
              <div>
                <h2>全局指标</h2>
                <p>更新时间：{new Date(metrics.generated_at).toLocaleString()}</p>
              </div>
            </div>

            <dl className="metrics-grid">
              <Metric label="我的清级" value={`L${user.security_level}`} />
              <Metric label="会话" value={metrics.conversation_count} />
              <Metric label="消息" value={metrics.message_count} />
              <Metric label="检索日志" value={metrics.retrieval_log_count} />
              <Metric label="LLM 调用" value={metrics.llm_call_count} />
              <Metric label="Token" value={metrics.total_tokens} />
              <Metric label="平均 LLM 耗时" value={formatNullable(metrics.average_llm_latency_ms, " ms")} />
              <Metric label="Fallback" value={metrics.fallback_call_count} />
              <Metric label="反馈" value={metrics.feedback_count} />
              <Metric label="点赞" value={metrics.positive_feedback_count} />
              <Metric label="点踩" value={metrics.negative_feedback_count} />
              <Metric label="点赞率" value={formatPercent(metrics.positive_feedback_rate)} />
              <Metric label="平均引用片段" value={formatNullable(metrics.average_selected_chunks)} />
            </dl>

            <section className="document-section">
              <div className="section-heading">
                <div>
                  <h2>数据源治理</h2>
                  <p>从这里进入知识库详情页维护公开知识库文档、密级或过期材料。</p>
                </div>
                <Link to="/knowledge-bases">管理知识库</Link>
              </div>

              {knowledgeBases.length === 0 && <p className="muted">暂无知识库。</p>}
              <div className="kb-list">
                {knowledgeBases.map((item) => (
                  <article className="kb-item" key={item.id}>
                    <div>
                      <h3>
                        <Link to={`/knowledge-bases/${item.id}`}>{item.name}</Link>
                      </h3>
                      <p>{item.description || "暂无描述"}</p>
                      <span className="badge">{item.role}</span>
                    </div>
                    <Link to={`/knowledge-bases/${item.id}`}>进入</Link>
                  </article>
                ))}
              </div>
            </section>

            <section className="document-section">
              <div className="section-heading">
                <h2>最近 LLM 错误</h2>
                <span>{metrics.recent_llm_errors.length}</span>
              </div>
              {metrics.recent_llm_errors.length === 0 && <p className="muted">暂无错误。</p>}
              <div className="trace-list">
                {metrics.recent_llm_errors.map((item) => (
                  <article className="trace-item fallback" key={String(item.id)}>
                    <strong>
                      {String(item.provider)} / {String(item.model_name)}
                    </strong>
                    <p>{String(item.error_message || "-")}</p>
                    <small>{new Date(String(item.created_at)).toLocaleString()}</small>
                  </article>
                ))}
              </div>
            </section>

            <section className="document-section">
              <div className="section-heading">
                <div>
                  <h2>组织部门</h2>
                  <p>部门用于限定部门知识库的默认可见边界；L1-L5 继续作为文档清级。</p>
                </div>
                <span>{departments.length}</span>
              </div>

              <form className="department-form" onSubmit={(event) => void handleCreateDepartment(event)}>
                <input
                  value={departmentName}
                  onChange={(event) => setDepartmentName(event.target.value)}
                  placeholder="部门名称，例如：运营部"
                  maxLength={120}
                />
                <input
                  value={departmentDescription}
                  onChange={(event) => setDepartmentDescription(event.target.value)}
                  placeholder="部门说明，可选"
                  maxLength={1000}
                />
                <button type="submit" disabled={usersLoading || !departmentName.trim()}>
                  创建部门
                </button>
              </form>

              {departments.length === 0 && <p className="muted">暂无部门。</p>}
              <div className="department-list">
                {departments.map((department) => (
                  <article className="department-row" key={department.id}>
                    <strong>{department.name}</strong>
                    <p>{department.description || "暂无说明"}</p>
                  </article>
                ))}
              </div>
            </section>

            <section className="document-section">
              <div className="section-heading">
                <div>
                  <h2>用户治理</h2>
                  <p>部门决定可访问的部门库，清级决定可读取的文档密级。</p>
                </div>
                <span>{users.length}</span>
              </div>

              <div className="user-governance-list">
                {users.map((item) => (
                  <article className="user-governance-row" key={item.id}>
                    <div>
                      <strong>{item.username}</strong>
                      <p>{item.email}</p>
                      <small>
                        {item.is_admin ? "admin" : "user"} · {item.is_active ? "active" : "inactive"} ·{" "}
                        {item.department_name || "未设置部门"}
                      </small>
                    </div>
                    <label>
                      <span>部门</span>
                      <select
                        value={item.department_id || ""}
                        disabled={usersLoading}
                        onChange={(event) => void handleDepartmentChange(item, event.target.value)}
                      >
                        <option value="">未设置</option>
                        {departments.map((department) => (
                          <option value={department.id} key={department.id}>
                            {department.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      <span>清级</span>
                      <select
                        value={item.security_level}
                        disabled={usersLoading}
                        onChange={(event) => void handleSecurityLevelChange(item, Number(event.target.value))}
                      >
                        {[1, 2, 3, 4, 5].map((level) => (
                          <option value={level} key={level}>
                            L{level}
                          </option>
                        ))}
                      </select>
                    </label>
                  </article>
                ))}
              </div>
            </section>

            <section className="document-section">
              <div className="section-heading">
                <div>
                  <h2>安全审计</h2>
                  <p>最近全局关键操作。</p>
                </div>
                <span>{auditLogs.length}</span>
              </div>

              {auditLogs.length === 0 && <p className="muted">暂无审计记录。</p>}
              <div className="audit-log-list">
                {auditLogs.map((log) => (
                  <article className={`audit-log-row ${log.outcome}`} key={log.id}>
                    <div>
                      <strong>{log.action}</strong>
                      <p>
                        {log.resource_type}
                        {log.resource_id ? ` · ${log.resource_id}` : ""}
                      </p>
                      {log.detail && <small>{log.detail}</small>}
                    </div>
                    <div>
                      <span className={`status-pill ${log.outcome}`}>{log.outcome}</span>
                      <small>
                        {log.security_level ? `L${log.security_level} · ` : ""}
                        {new Date(log.created_at).toLocaleString()}
                      </small>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          </>
        )}
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function formatNullable(value: number | null, suffix = ""): string {
  if (value === null) {
    return "-";
  }
  return `${value}${suffix}`;
}

function formatPercent(value: number | null): string {
  if (value === null) {
    return "-";
  }
  return `${(value * 100).toFixed(1)}%`;
}
