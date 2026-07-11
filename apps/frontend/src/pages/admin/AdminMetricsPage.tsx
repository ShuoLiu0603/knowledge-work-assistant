import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  createDepartment,
  fetchAdminMetrics,
  listAdminUsers,
  listAuditLogs,
  listDepartments,
  listExternalCleanupJobs,
  listKnowledgeBases,
  runOperationalRetention,
  retryExternalCleanupJob,
  updateAdminUser,
  type AdminMetrics,
  type AdminUser,
  type AuditLog,
  type Department,
  type ExternalCleanupJob,
  type KnowledgeBase,
  type RetentionRun,
  type User,
} from "../../lib/api";
import { AppShell } from "../../components/layout/AppShell";
import { TopBar } from "../../components/layout/TopBar";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Input } from "../../components/ui/Input";
import page from "../../styles/workspace.module.css";

type Props = { token: string; user: User; onLogout: () => Promise<void> };
type Tab = "overview" | "users" | "departments" | "cleanup" | "retention" | "audit";

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "总览" },
  { key: "users", label: "用户" },
  { key: "departments", label: "部门" },
  { key: "cleanup", label: "清理任务" },
  { key: "retention", label: "保留策略" },
  { key: "audit", label: "审计日志" },
];

const STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  processing: "处理中",
  completed: "已完成",
  failed: "失败",
  success: "成功",
  denied: "已拒绝",
  allowed: "已允许",
};

const VISIBILITY_LABELS: Record<KnowledgeBase["visibility"], string> = {
  private: "私有",
  department: "部门可见",
  public: "公开",
};

const KB_ROLE_LABELS: Record<KnowledgeBase["role"], string> = {
  owner: "所有者",
  editor: "编辑者",
  viewer: "查看者",
};

const RETENTION_LABELS: Record<string, string> = {
  llm_call_logs: "LLM 调用日志",
  retrieval_logs: "检索日志",
  agent_runs: "智能体运行记录",
  memory_recall_logs: "记忆召回日志",
  memory_update_jobs: "记忆更新任务",
  external_cleanup_jobs: "外部资源清理任务",
};

const AUDIT_ACTION_LABELS: Record<string, string> = {
  "retention.dry_run": "保留策略预演",
  "retention.apply": "执行保留策略",
  "document.external_cleanup": "文档外部资源清理",
  "knowledge_base.external_cleanup": "知识库外部资源清理",
  "memory.external_cleanup": "记忆外部资源清理",
};

const RESOURCE_TYPE_LABELS: Record<string, string> = {
  document: "文档",
  knowledge_base: "知识库",
  memory: "记忆",
  retention: "保留策略",
};

function fmtNullable(value: number | null, suffix = ""): string {
  return value == null ? "-" : `${value}${suffix}`;
}

function shortId(value: string | null): string {
  return value ? value.slice(0, 8) : "-";
}

function statusStyle(status: string): React.CSSProperties {
  if (status === "failed" || status === "denied") return { background: "var(--color-danger)", color: "#fff" };
  if (status === "completed" || status === "success" || status === "allowed") return { background: "var(--color-success)", color: "#fff" };
  if (status === "processing") return { background: "var(--color-info)", color: "#fff" };
  return { background: "var(--color-warning)", color: "#fff" };
}

function statusPill(status: string) {
  return (
    <span style={{ display: "inline-block", padding: "2px 8px", borderRadius: 999, fontSize: 11, fontWeight: 760, ...statusStyle(status) }}>
      {statusLabel(status)}
    </span>
  );
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function visibilityLabel(visibility: KnowledgeBase["visibility"]): string {
  return VISIBILITY_LABELS[visibility] ?? visibility;
}

function kbRoleLabel(role: KnowledgeBase["role"]): string {
  return KB_ROLE_LABELS[role] ?? role;
}

function auditActionLabel(action: string): string {
  return AUDIT_ACTION_LABELS[action] ?? action;
}

function resourceTypeLabel(resourceType: string): string {
  return RESOURCE_TYPE_LABELS[resourceType] ?? resourceType;
}

function retentionLabel(key: string): string {
  return RETENTION_LABELS[key] ?? key;
}

export function AdminMetricsPage({ token, user, onLogout }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [metrics, setMetrics] = useState<AdminMetrics | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [cleanupJobs, setCleanupJobs] = useState<ExternalCleanupJob[]>([]);
  const [retentionRun, setRetentionRun] = useState<RetentionRun | null>(null);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [departmentName, setDepartmentName] = useState("");
  const [departmentDescription, setDepartmentDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmRetentionApply, setConfirmRetentionApply] = useState(false);

  async function loadAdminData(active = true) {
    const [m, u, a, jobs, k, d] = await Promise.all([
      fetchAdminMetrics(token),
      listAdminUsers(token),
      listAuditLogs(token, 50),
      listExternalCleanupJobs(token, { limit: 100 }),
      listKnowledgeBases(token),
      listDepartments(token),
    ]);
    if (!active) return;
    setMetrics(m);
    setUsers(u);
    setAuditLogs(a);
    setCleanupJobs(jobs);
    setKnowledgeBases(k);
    setDepartments(d);
    setError("");
  }

  useEffect(() => {
    let active = true;
    loadAdminData(active)
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : "管理数据加载失败。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [token]);

  const metricItems: [string, string | number][] = useMemo(() => {
    if (!metrics) return [];
    return [
      ["会话数", metrics.conversation_count],
      ["消息数", metrics.message_count],
      ["检索日志", metrics.retrieval_log_count],
      ["LLM 调用", metrics.llm_call_count],
      ["Token 总量", metrics.total_tokens],
      ["平均 LLM 延迟", fmtNullable(metrics.average_llm_latency_ms, " ms")],
      ["兜底调用", metrics.fallback_call_count],
      ["平均选中分块", fmtNullable(metrics.average_selected_chunks)],
      ["清理任务", metrics.external_cleanup_job_count],
      ["失败清理任务", metrics.failed_external_cleanup_job_count],
      ["排队清理任务", metrics.queued_external_cleanup_job_count],
    ];
  }, [metrics]);

  async function updateUserDepartment(item: AdminUser, departmentId: string) {
    setBusy(true);
    setError("");
    try {
      const updated = await updateAdminUser(token, item.id, { department_id: departmentId || null });
      setUsers((previous) => previous.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "用户更新失败。");
    } finally {
      setBusy(false);
    }
  }

  async function updateUserSecurityLevel(item: AdminUser, securityLevel: number) {
    setBusy(true);
    setError("");
    try {
      const updated = await updateAdminUser(token, item.id, { security_level: securityLevel });
      setUsers((previous) => previous.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "用户更新失败。");
    } finally {
      setBusy(false);
    }
  }

  async function retryCleanupJob(job: ExternalCleanupJob) {
    setBusy(true);
    setError("");
    try {
      const updated = await retryExternalCleanupJob(token, job.id);
      setCleanupJobs((previous) => previous.map((entry) => (entry.id === updated.id ? updated : entry)));
      const refreshedMetrics = await fetchAdminMetrics(token);
      setMetrics(refreshedMetrics);
    } catch (e) {
      setError(e instanceof Error ? e.message : "清理任务重试失败。");
    } finally {
      setBusy(false);
    }
  }

  async function runRetention(dryRun: boolean) {
    setBusy(true);
    setError("");
    try {
      const result = await runOperationalRetention(token, dryRun);
      setRetentionRun(result);
      await loadAdminData(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保留策略执行失败。");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateDepartment(event: FormEvent) {
    event.preventDefault();
    if (!departmentName.trim()) return;
    setBusy(true);
    setError("");
    try {
      const department = await createDepartment(token, {
        name: departmentName.trim(),
        description: departmentDescription.trim() || null,
      });
      setDepartments((previous) => [...previous, department].sort((left, right) => left.name.localeCompare(right.name)));
      setDepartmentName("");
      setDepartmentDescription("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "部门创建失败。");
    } finally {
      setBusy(false);
    }
  }

  const tabBar = (
    <div className={page.tabs}>
      {TABS.map((item) => (
        <button
          key={item.key}
          type="button"
          onClick={() => setTab(item.key)}
          className={`${page.tab} ${tab === item.key ? page.tabActive : ""}`}
        >
          {item.label}
        </button>
      ))}
    </div>
  );

  return (
    <AppShell>
      <TopBar user={{ username: user.username, is_admin: user.is_admin }} onLogout={onLogout} />
      <section className={page.page}>
        <div className={page.pageHeader}>
          <div>
            <h1 className={page.title}>运维管理台</h1>
            <p className={page.subtitle}>统一查看全局用量、访问控制、审计轨迹与外部资源清理恢复。</p>
          </div>
          {metrics && <p className={page.muted} style={{ margin: 0, fontSize: 12 }}>更新于 {new Date(metrics.generated_at).toLocaleString()}</p>}
        </div>

        {loading && <p className={page.muted}>正在加载管理数据...</p>}
        {error && <p className={page.error}>{error}</p>}

        {tabBar}

        {tab === "overview" && metrics && (
          <>
            <div className={page.statsGrid}>
              <div className={page.statCard} style={{ background: "var(--color-primary-soft)" }}>
                <div className={page.statLabel}>我的权限等级</div>
                <div className={page.statValue} style={{ color: "var(--color-primary-strong)" }}>L{user.security_level}</div>
              </div>
              <div className={page.statCard}>
                <div className={page.statLabel}>知识库</div>
                <div className={page.statValue}>{knowledgeBases.length}</div>
              </div>
              <div className={page.statCard}>
                <div className={page.statLabel}>部门</div>
                <div className={page.statValue}>{departments.length}</div>
              </div>
              <div className={page.statCard}>
                <div className={page.statLabel}>用户</div>
                <div className={page.statValue}>{users.length}</div>
              </div>
            </div>

            <h2 className={page.sectionTitle} style={{ marginBottom: 10 }}>系统指标</h2>
            <div className={page.statsGrid}>
              {metricItems.map(([label, value]) => (
                <div key={label} className={page.statCard}>
                  <dt className={page.statLabel}>{label}</dt>
                  <dd style={{ margin: 0, fontSize: 20, fontWeight: 760 }}>{value}</dd>
                </div>
              ))}
            </div>

            <h2 className={page.sectionTitle} style={{ marginBottom: 10 }}>知识库</h2>
            <div className={page.cardList} style={{ marginBottom: 24 }}>
              {knowledgeBases.length === 0 && <p className={page.empty}>暂无知识库。</p>}
              {knowledgeBases.map((kb) => (
                <div key={kb.id} className={page.card} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "12px 14px" }}>
                  <div>
                    <strong>{kb.name}</strong>
                    <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--color-text-muted)" }}>
                      {visibilityLabel(kb.visibility)} {kb.department_name ? `- ${kb.department_name}` : ""} - {kbRoleLabel(kb.role)}
                    </p>
                  </div>
                  <Link to={`/knowledge-bases/${kb.id}`}>打开</Link>
                </div>
              ))}
            </div>

            <h2 className={page.sectionTitle} style={{ marginBottom: 10 }}>近期 LLM 错误</h2>
            <div className={page.cardList}>
              {metrics.recent_llm_errors.length === 0 && <p className={page.empty}>近期没有 LLM 错误。</p>}
              {metrics.recent_llm_errors.map((entry, index) => (
                <div key={`${entry.id ?? index}`} className={page.card} style={{ padding: "10px 12px", borderColor: "rgba(180, 35, 24, 0.18)" }}>
                  <strong>{String(entry.provider ?? "-")} / {String(entry.model_name ?? "-")}</strong>
                  <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--color-danger)" }}>{String(entry.error_message ?? "-")}</p>
                </div>
              ))}
            </div>
          </>
        )}

        {tab === "users" && (
          <div>
            <div className={page.toolbar}>
              <h2 className={page.sectionTitle}>用户（{users.length}）</h2>
            </div>
            <div className={page.cardList}>
              {users.length === 0 && <p className={page.empty}>暂无用户。</p>}
              {users.map((item) => (
                <div key={item.id} className={page.card} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 16px", flexWrap: "wrap", gap: 10 }}>
                  <div>
                    <strong>{item.username}</strong>
                    <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--color-text-muted)" }}>
                      {item.email} - {item.is_admin ? "管理员" : "普通用户"} - {item.department_name || "未分配部门"}
                    </p>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <select value={item.department_id || ""} disabled={busy} onChange={(event) => updateUserDepartment(item, event.target.value)} style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--color-border)", fontSize: 12, fontFamily: "inherit" }}>
                      <option value="">未分配部门</option>
                      {departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}
                    </select>
                    <select value={item.security_level} disabled={busy} onChange={(event) => updateUserSecurityLevel(item, Number(event.target.value))} style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--color-border)", fontSize: 12, fontFamily: "inherit" }}>
                      {[1, 2, 3, 4, 5].map((level) => <option key={level} value={level}>L{level}</option>)}
                    </select>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "departments" && (
          <div>
            <div className={page.toolbar}>
              <h2 className={page.sectionTitle}>部门（{departments.length}）</h2>
            </div>
            <form onSubmit={handleCreateDepartment} style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
              <Input value={departmentName} onChange={(event) => setDepartmentName(event.target.value)} placeholder="部门名称" maxLength={120} />
              <Input value={departmentDescription} onChange={(event) => setDepartmentDescription(event.target.value)} placeholder="部门说明" maxLength={1000} />
              <Button type="submit" size="sm" disabled={busy || !departmentName.trim()}>创建部门</Button>
            </form>
            <div className={page.cardList}>
              {departments.length === 0 && <p className={page.empty}>暂无部门。</p>}
              {departments.map((department) => (
                <div key={department.id} className={page.card} style={{ padding: "12px 16px" }}>
                  <strong>{department.name}</strong>
                  <span style={{ marginLeft: 12, fontSize: 13, color: "var(--color-text-muted)" }}>{department.description || "暂无说明"}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "cleanup" && (
          <div>
            <div className={page.toolbar}>
              <h2 className={page.sectionTitle}>清理任务（{cleanupJobs.length}）</h2>
              <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => loadAdminData(true)}>
                刷新
              </Button>
            </div>
            <div className={page.cardList}>
              {cleanupJobs.length === 0 && <p className={page.empty}>暂无清理任务。</p>}
              {cleanupJobs.map((job) => (
                <div key={job.id} className={page.card} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 12, padding: "12px 14px" }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <strong>{job.resource_type}</strong>
                      {statusPill(job.status)}
                      <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>{shortId(job.resource_id)}</span>
                    </div>
                    <p style={{ margin: "4px 0 0", color: "var(--color-text-muted)", fontSize: 12 }}>
                      尝试 {job.attempts} 次 - 对象 {job.object_keys.length} 个 - 更新于 {new Date(job.updated_at).toLocaleString()}
                    </p>
                    {job.error_message && <p style={{ margin: "4px 0 0", color: "var(--color-danger)", fontSize: 12 }}>{job.error_message}</p>}
                  </div>
                  <div style={{ display: "flex", alignItems: "center" }}>
                    <Button
                      type="button"
                      size="sm"
                      variant={job.status === "failed" || job.status === "queued" ? "primary" : "secondary"}
                      disabled={busy || job.status === "completed" || job.status === "processing"}
                      onClick={() => retryCleanupJob(job)}
                    >
                      重试
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "retention" && (
          <div>
            <div className={page.toolbar}>
              <div>
                <h2 className={page.sectionTitle}>运维数据保留策略</h2>
                <p className={page.subtitle}>
                  预览并执行 LLM 日志、检索日志、智能体运行记录、记忆召回日志、记忆更新任务和已完成清理任务的保留窗口。
                </p>
              </div>
              <div className={page.controlRow}>
                <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => runRetention(true)}>
                  预演
                </Button>
                <Button type="button" size="sm" variant="danger" disabled={busy} onClick={() => setConfirmRetentionApply(true)}>
                  执行清理
                </Button>
              </div>
            </div>

            {!retentionRun && <p className={page.empty}>先运行一次预演，查看保留策略会影响哪些记录。</p>}
            {retentionRun && (
              <>
                <p className={page.muted} style={{ marginTop: 0 }}>
                  最近运行：{new Date(retentionRun.generated_at).toLocaleString()} - {retentionRun.dry_run ? "预演" : "已执行"}
                </p>
                <div className={page.cardList}>
                  {Object.entries(retentionRun.deleted_counts).map(([key, count]) => (
                    <div key={key} className={page.card} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "12px 14px" }}>
                      <div>
                        <strong>{retentionLabel(key)}</strong>
                        <p style={{ margin: "3px 0 0", color: "var(--color-text-muted)", fontSize: 12 }}>
                          截止时间：{retentionRun.cutoffs[key] ? new Date(retentionRun.cutoffs[key] as string).toLocaleString() : "未启用"}
                        </p>
                      </div>
                      <span style={{ fontSize: 20, fontWeight: 780 }}>{count}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {tab === "audit" && (
          <div>
            <div className={page.toolbar}>
              <h2 className={page.sectionTitle}>审计日志（{auditLogs.length}）</h2>
            </div>
            <div className={page.cardList}>
              {auditLogs.length === 0 && <p className={page.empty}>暂无审计日志。</p>}
              {auditLogs.slice(0, 30).map((log) => (
                <div key={log.id} className={page.card} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "12px 14px", background: log.outcome === "denied" ? "var(--color-danger-soft)" : "var(--color-surface)", fontSize: 13 }}>
                  <div>
                    <strong>{auditActionLabel(log.action)}</strong>
                    <span style={{ marginLeft: 8, color: "var(--color-text-secondary)" }}>
                      {resourceTypeLabel(log.resource_type)}{log.resource_id ? ` - ${shortId(log.resource_id)}` : ""}
                    </span>
                    {log.detail && <p style={{ margin: "2px 0 0", color: "var(--color-text-muted)" }}>{log.detail}</p>}
                  </div>
                  <div style={{ textAlign: "right", flexShrink: 0 }}>
                    {statusPill(log.outcome)}
                    <div style={{ fontSize: 10, color: "var(--color-text-muted)", marginTop: 2 }}>
                      {log.security_level ? `L${log.security_level} - ` : ""}{new Date(log.created_at).toLocaleString()}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>
      {confirmRetentionApply && (
        <ConfirmDialog
          title="执行保留策略"
          message="此操作会按照当前保留窗口删除已过期的运维日志和已完成的后台任务记录。审计日志、会话、文档、知识库和已保存记忆都会保留。"
          confirmLabel="执行清理"
          cancelLabel="取消"
          onConfirm={() => {
            setConfirmRetentionApply(false);
            void runRetention(false);
          }}
          onCancel={() => setConfirmRetentionApply(false)}
        />
      )}
    </AppShell>
  );
}
