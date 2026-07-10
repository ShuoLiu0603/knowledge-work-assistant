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
  { key: "overview", label: "Overview" },
  { key: "users", label: "Users" },
  { key: "departments", label: "Departments" },
  { key: "cleanup", label: "Cleanup jobs" },
  { key: "retention", label: "Retention" },
  { key: "audit", label: "Audit logs" },
];

function fmtNullable(value: number | null, suffix = ""): string {
  return value == null ? "-" : `${value}${suffix}`;
}

function shortId(value: string | null): string {
  return value ? value.slice(0, 8) : "-";
}

function statusStyle(status: string): React.CSSProperties {
  if (status === "failed") return { background: "var(--color-danger)", color: "#fff" };
  if (status === "completed") return { background: "var(--color-success)", color: "#fff" };
  if (status === "processing") return { background: "var(--color-info)", color: "#fff" };
  return { background: "var(--color-warning)", color: "#fff" };
}

function statusPill(status: string) {
  return (
    <span style={{ display: "inline-block", padding: "2px 8px", borderRadius: 999, fontSize: 11, fontWeight: 760, ...statusStyle(status) }}>
      {status}
    </span>
  );
}

function retentionLabel(key: string): string {
  return key
    .split("_")
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ");
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
        if (active) setError(e instanceof Error ? e.message : "Failed to load admin data.");
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
      ["Conversations", metrics.conversation_count],
      ["Messages", metrics.message_count],
      ["Retrieval logs", metrics.retrieval_log_count],
      ["LLM calls", metrics.llm_call_count],
      ["Tokens", metrics.total_tokens],
      ["Avg. LLM latency", fmtNullable(metrics.average_llm_latency_ms, " ms")],
      ["Fallback calls", metrics.fallback_call_count],
      ["Avg. selected chunks", fmtNullable(metrics.average_selected_chunks)],
      ["Cleanup jobs", metrics.external_cleanup_job_count],
      ["Failed cleanup", metrics.failed_external_cleanup_job_count],
      ["Queued cleanup", metrics.queued_external_cleanup_job_count],
    ];
  }, [metrics]);

  async function updateUserDepartment(item: AdminUser, departmentId: string) {
    setBusy(true);
    setError("");
    try {
      const updated = await updateAdminUser(token, item.id, { department_id: departmentId || null });
      setUsers((previous) => previous.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update user.");
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
      setError(e instanceof Error ? e.message : "Failed to update user.");
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
      setError(e instanceof Error ? e.message : "Failed to retry cleanup job.");
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
      setError(e instanceof Error ? e.message : "Failed to run retention policy.");
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
      setError(e instanceof Error ? e.message : "Failed to create department.");
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
            <h1 className={page.title}>Operations console</h1>
            <p className={page.subtitle}>Global usage, access control, audit trail, and external cleanup recovery.</p>
          </div>
          {metrics && <p className={page.muted} style={{ margin: 0, fontSize: 12 }}>Updated {new Date(metrics.generated_at).toLocaleString()}</p>}
        </div>

        {loading && <p className={page.muted}>Loading admin data...</p>}
        {error && <p className={page.error}>{error}</p>}

        {tabBar}

        {tab === "overview" && metrics && (
          <>
            <div className={page.statsGrid}>
              <div className={page.statCard} style={{ background: "var(--color-primary-soft)" }}>
                <div className={page.statLabel}>Your clearance</div>
                <div className={page.statValue} style={{ color: "var(--color-primary-strong)" }}>L{user.security_level}</div>
              </div>
              <div className={page.statCard}>
                <div className={page.statLabel}>Knowledge bases</div>
                <div className={page.statValue}>{knowledgeBases.length}</div>
              </div>
              <div className={page.statCard}>
                <div className={page.statLabel}>Departments</div>
                <div className={page.statValue}>{departments.length}</div>
              </div>
              <div className={page.statCard}>
                <div className={page.statLabel}>Users</div>
                <div className={page.statValue}>{users.length}</div>
              </div>
            </div>

            <h2 className={page.sectionTitle} style={{ marginBottom: 10 }}>System metrics</h2>
            <div className={page.statsGrid}>
              {metricItems.map(([label, value]) => (
                <div key={label} className={page.statCard}>
                  <dt className={page.statLabel}>{label}</dt>
                  <dd style={{ margin: 0, fontSize: 20, fontWeight: 760 }}>{value}</dd>
                </div>
              ))}
            </div>

            <h2 className={page.sectionTitle} style={{ marginBottom: 10 }}>Knowledge bases</h2>
            <div className={page.cardList} style={{ marginBottom: 24 }}>
              {knowledgeBases.length === 0 && <p className={page.empty}>No knowledge bases yet.</p>}
              {knowledgeBases.map((kb) => (
                <div key={kb.id} className={page.card} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "12px 14px" }}>
                  <div>
                    <strong>{kb.name}</strong>
                    <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--color-text-muted)" }}>
                      {kb.visibility} {kb.department_name ? `- ${kb.department_name}` : ""} - {kb.role}
                    </p>
                  </div>
                  <Link to={`/knowledge-bases/${kb.id}`}>Open</Link>
                </div>
              ))}
            </div>

            <h2 className={page.sectionTitle} style={{ marginBottom: 10 }}>Recent LLM errors</h2>
            <div className={page.cardList}>
              {metrics.recent_llm_errors.length === 0 && <p className={page.empty}>No recent LLM errors.</p>}
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
              <h2 className={page.sectionTitle}>Users ({users.length})</h2>
            </div>
            <div className={page.cardList}>
              {users.length === 0 && <p className={page.empty}>No users.</p>}
              {users.map((item) => (
                <div key={item.id} className={page.card} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 16px", flexWrap: "wrap", gap: 10 }}>
                  <div>
                    <strong>{item.username}</strong>
                    <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--color-text-muted)" }}>
                      {item.email} - {item.is_admin ? "admin" : "user"} - {item.department_name || "No department"}
                    </p>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <select value={item.department_id || ""} disabled={busy} onChange={(event) => updateUserDepartment(item, event.target.value)} style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--color-border)", fontSize: 12, fontFamily: "inherit" }}>
                      <option value="">No department</option>
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
              <h2 className={page.sectionTitle}>Departments ({departments.length})</h2>
            </div>
            <form onSubmit={handleCreateDepartment} style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
              <Input value={departmentName} onChange={(event) => setDepartmentName(event.target.value)} placeholder="Department name" maxLength={120} />
              <Input value={departmentDescription} onChange={(event) => setDepartmentDescription(event.target.value)} placeholder="Description" maxLength={1000} />
              <Button type="submit" size="sm" disabled={busy || !departmentName.trim()}>Create department</Button>
            </form>
            <div className={page.cardList}>
              {departments.length === 0 && <p className={page.empty}>No departments.</p>}
              {departments.map((department) => (
                <div key={department.id} className={page.card} style={{ padding: "12px 16px" }}>
                  <strong>{department.name}</strong>
                  <span style={{ marginLeft: 12, fontSize: 13, color: "var(--color-text-muted)" }}>{department.description || "No description"}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "cleanup" && (
          <div>
            <div className={page.toolbar}>
              <h2 className={page.sectionTitle}>Cleanup jobs ({cleanupJobs.length})</h2>
              <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => loadAdminData(true)}>
                Refresh
              </Button>
            </div>
            <div className={page.cardList}>
              {cleanupJobs.length === 0 && <p className={page.empty}>No cleanup jobs.</p>}
              {cleanupJobs.map((job) => (
                <div key={job.id} className={page.card} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 12, padding: "12px 14px" }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <strong>{job.resource_type}</strong>
                      {statusPill(job.status)}
                      <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>{shortId(job.resource_id)}</span>
                    </div>
                    <p style={{ margin: "4px 0 0", color: "var(--color-text-muted)", fontSize: 12 }}>
                      Attempts {job.attempts} - Objects {job.object_keys.length} - Updated {new Date(job.updated_at).toLocaleString()}
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
                      Retry
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
                <h2 className={page.sectionTitle}>Operational retention</h2>
                <p className={page.subtitle}>
                  Preview and apply configured retention for LLM logs, retrieval logs, agent runs, memory recall logs,
                  memory update jobs, and completed cleanup jobs.
                </p>
              </div>
              <div className={page.controlRow}>
                <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => runRetention(true)}>
                  Dry run
                </Button>
                <Button type="button" size="sm" variant="danger" disabled={busy} onClick={() => setConfirmRetentionApply(true)}>
                  Apply retention
                </Button>
              </div>
            </div>

            {!retentionRun && <p className={page.empty}>Run a dry-run to preview retention impact.</p>}
            {retentionRun && (
              <>
                <p className={page.muted} style={{ marginTop: 0 }}>
                  Last run: {new Date(retentionRun.generated_at).toLocaleString()} - {retentionRun.dry_run ? "dry run" : "applied"}
                </p>
                <div className={page.cardList}>
                  {Object.entries(retentionRun.deleted_counts).map(([key, count]) => (
                    <div key={key} className={page.card} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "12px 14px" }}>
                      <div>
                        <strong>{retentionLabel(key)}</strong>
                        <p style={{ margin: "3px 0 0", color: "var(--color-text-muted)", fontSize: 12 }}>
                          Cutoff: {retentionRun.cutoffs[key] ? new Date(retentionRun.cutoffs[key] as string).toLocaleString() : "disabled"}
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
              <h2 className={page.sectionTitle}>Audit logs ({auditLogs.length})</h2>
            </div>
            <div className={page.cardList}>
              {auditLogs.length === 0 && <p className={page.empty}>No audit logs.</p>}
              {auditLogs.slice(0, 30).map((log) => (
                <div key={log.id} className={page.card} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "12px 14px", background: log.outcome === "denied" ? "var(--color-danger-soft)" : "var(--color-surface)", fontSize: 13 }}>
                  <div>
                    <strong>{log.action}</strong>
                    <span style={{ marginLeft: 8, color: "var(--color-text-secondary)" }}>
                      {log.resource_type}{log.resource_id ? ` - ${shortId(log.resource_id)}` : ""}
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
          title="Apply retention policy"
          message="This deletes expired operational logs and completed background job records according to the configured retention windows. Audit logs, conversations, documents, knowledge bases, and saved memories are preserved."
          confirmLabel="Apply"
          cancelLabel="Cancel"
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
