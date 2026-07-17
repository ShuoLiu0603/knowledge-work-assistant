import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  createAdminUser,
  createDepartment,
  deleteAdminUser,
  deleteDepartment,
  fetchAdminMetrics,
  listAdminUsers,
  listDepartments,
  listKnowledgeBases,
  updateAdminUser,
  updateDepartmentAdmin,
  type AdminMetrics,
  type AdminUser,
  type Department,
  type KnowledgeBase,
  type User,
} from "../../lib/api";
import { AppShell } from "../../components/layout/AppShell";
import { TopBar } from "../../components/layout/TopBar";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Input } from "../../components/ui/Input";
import page from "../../styles/workspace.module.css";

type Props = { token: string; user: User; onLogout: () => Promise<void> };
type Tab = "overview" | "users" | "departments";

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "总览" },
  { key: "users", label: "用户" },
  { key: "departments", label: "部门" },
];

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

function fmtNullable(value: number | null, suffix = ""): string {
  return value == null ? "-" : `${value}${suffix}`;
}

function visibilityLabel(visibility: KnowledgeBase["visibility"]): string {
  return VISIBILITY_LABELS[visibility] ?? visibility;
}

function kbRoleLabel(role: KnowledgeBase["role"]): string {
  return KB_ROLE_LABELS[role] ?? role;
}

export function AdminMetricsPage({ token, user, onLogout }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [metrics, setMetrics] = useState<AdminMetrics | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [departmentName, setDepartmentName] = useState("");
  const [departmentDescription, setDepartmentDescription] = useState("");
  const [departmentAdminUserId, setDepartmentAdminUserId] = useState("");
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserDepartmentId, setNewUserDepartmentId] = useState("");
  const [newUserSecurityLevel, setNewUserSecurityLevel] = useState(1);
  const [newUserIsAdmin, setNewUserIsAdmin] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmDeleteUser, setConfirmDeleteUser] = useState<AdminUser | null>(null);
  const [confirmDeleteDepartment, setConfirmDeleteDepartment] = useState<Department | null>(null);

  async function loadAdminData(active = true) {
    const [m, u, k, d] = await Promise.all([
      fetchAdminMetrics(token),
      listAdminUsers(token),
      listKnowledgeBases(token),
      listDepartments(token),
    ]);
    if (!active) return;
    setMetrics(m);
    setUsers(u);
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
    ];
  }, [metrics]);

  const availableNewDepartmentAdmins = users.filter(
    (item) => item.id !== user.id && item.is_active && !item.department_id,
  );

  function departmentAdminCandidates(department: Department): AdminUser[] {
    return users.filter(
      (item) =>
        item.id === department.admin_user_id
        || (
          item.id !== user.id
          && item.is_active
          && (!item.department_id || item.department_id === department.id)
        ),
    );
  }

  async function handleCreateUser(event: FormEvent) {
    event.preventDefault();
    if (!newUserEmail.trim() || !newUserName.trim() || !newUserPassword) return;
    setBusy(true);
    setError("");
    try {
      const created = await createAdminUser(token, {
        email: newUserEmail.trim(),
        username: newUserName.trim(),
        password: newUserPassword,
        is_admin: newUserIsAdmin,
        security_level: newUserSecurityLevel,
        department_id: newUserDepartmentId || null,
      });
      setUsers((previous) => [created, ...previous]);
      setNewUserEmail("");
      setNewUserName("");
      setNewUserPassword("");
      setNewUserDepartmentId("");
      setNewUserSecurityLevel(1);
      setNewUserIsAdmin(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "账号创建失败。");
    } finally {
      setBusy(false);
    }
  }

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

  async function updateUserRole(item: AdminUser, isAdmin: boolean) {
    setBusy(true);
    setError("");
    try {
      const updated = await updateAdminUser(token, item.id, { is_admin: isAdmin });
      setUsers((previous) => previous.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "用户角色更新失败。");
    } finally {
      setBusy(false);
    }
  }

  async function updateUserActive(item: AdminUser, isActive: boolean) {
    setBusy(true);
    setError("");
    try {
      const updated = await updateAdminUser(token, item.id, { is_active: isActive });
      setUsers((previous) => previous.map((entry) => (entry.id === updated.id ? updated : entry)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "账号状态更新失败。");
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteUser(item: AdminUser) {
    setBusy(true);
    setError("");
    try {
      await deleteAdminUser(token, item.id);
      await loadAdminData(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "账号删除失败。");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateDepartment(event: FormEvent) {
    event.preventDefault();
    if (!departmentName.trim() || !departmentAdminUserId) return;
    setBusy(true);
    setError("");
    try {
      const department = await createDepartment(token, {
        name: departmentName.trim(),
        description: departmentDescription.trim() || null,
        admin_user_id: departmentAdminUserId,
      });
      setDepartments((previous) => [...previous, department].sort((left, right) => left.name.localeCompare(right.name)));
      setUsers((previous) => previous.map((item) => (
        item.id === departmentAdminUserId
          ? {
              ...item,
              department_id: department.id,
              department_name: department.name,
              is_department_admin: true,
            }
          : item
      )));
      setDepartmentName("");
      setDepartmentDescription("");
      setDepartmentAdminUserId("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "部门创建失败。");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdateDepartmentAdmin(department: Department, adminUserId: string) {
    setBusy(true);
    setError("");
    try {
      const updated = await updateDepartmentAdmin(token, department.id, adminUserId);
      const refreshedUsers = await listAdminUsers(token);
      setDepartments((previous) => previous.map((item) => (item.id === updated.id ? updated : item)));
      setUsers(refreshedUsers);
    } catch (e) {
      setError(e instanceof Error ? e.message : "部门管理员更新失败。");
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteDepartment(department: Department) {
    setBusy(true);
    setError("");
    try {
      await deleteDepartment(token, department.id);
      await loadAdminData(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "部门删除失败。");
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
            <p className={page.subtitle}>统一查看全局用量，并管理系统成员、权限与部门负责人。</p>
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
            <form onSubmit={handleCreateUser} style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
              <Input value={newUserEmail} onChange={(event) => setNewUserEmail(event.target.value)} placeholder="邮箱" type="email" maxLength={255} />
              <Input value={newUserName} onChange={(event) => setNewUserName(event.target.value)} placeholder="姓名" maxLength={100} />
              <Input value={newUserPassword} onChange={(event) => setNewUserPassword(event.target.value)} placeholder="初始密码（至少 8 位）" type="password" maxLength={128} />
              <select value={newUserDepartmentId} disabled={busy} onChange={(event) => setNewUserDepartmentId(event.target.value)} style={{ padding: "8px", borderRadius: 4, border: "1px solid var(--color-border)", fontFamily: "inherit" }}>
                <option value="">未分配部门</option>
                {departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}
              </select>
              <select
                value={newUserIsAdmin ? "admin" : "member"}
                disabled={busy}
                onChange={(event) => {
                  const isAdmin = event.target.value === "admin";
                  setNewUserIsAdmin(isAdmin);
                  setNewUserSecurityLevel(isAdmin ? 5 : 1);
                }}
                style={{ padding: "8px", borderRadius: 4, border: "1px solid var(--color-border)", fontFamily: "inherit" }}
              >
                <option value="member">普通成员</option>
                <option value="admin">系统管理员</option>
              </select>
              <select value={newUserSecurityLevel} disabled={busy} onChange={(event) => setNewUserSecurityLevel(Number(event.target.value))} style={{ padding: "8px", borderRadius: 4, border: "1px solid var(--color-border)", fontFamily: "inherit" }}>
                {[1, 2, 3, 4, 5].map((level) => <option key={level} value={level}>L{level}</option>)}
              </select>
              <Button type="submit" size="sm" disabled={busy || !newUserEmail.trim() || !newUserName.trim() || newUserPassword.length < 8}>创建账号</Button>
            </form>
            <div className={page.cardList}>
              {users.length === 0 && <p className={page.empty}>暂无用户。</p>}
              {users.map((item) => (
                <div key={item.id} className={page.card} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 16px", flexWrap: "wrap", gap: 10 }}>
                  <div>
                    <strong>{item.username}</strong>
                    <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--color-text-muted)" }}>
                      {item.email} - {item.is_admin ? "系统管理员" : "普通成员"}
                      {item.is_department_admin ? " / 部门管理员" : ""} - {item.department_name || "未分配部门"}
                      {item.id === user.id ? " / 当前账号" : ""}
                      {!item.is_active ? " - 已停用" : ""}
                    </p>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <select value={item.department_id || ""} disabled={busy || item.id === user.id || item.is_department_admin} onChange={(event) => updateUserDepartment(item, event.target.value)} style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--color-border)", fontSize: 12, fontFamily: "inherit" }}>
                      <option value="">未分配部门</option>
                      {departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}
                    </select>
                    <select value={item.security_level} disabled={busy || item.id === user.id} onChange={(event) => updateUserSecurityLevel(item, Number(event.target.value))} style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--color-border)", fontSize: 12, fontFamily: "inherit" }}>
                      {[1, 2, 3, 4, 5].map((level) => <option key={level} value={level}>L{level}</option>)}
                    </select>
                    <select value={item.is_admin ? "admin" : "member"} disabled={busy || item.id === user.id} onChange={(event) => updateUserRole(item, event.target.value === "admin")} style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--color-border)", fontSize: 12, fontFamily: "inherit" }}>
                      <option value="member">普通成员</option>
                      <option value="admin">系统管理员</option>
                    </select>
                    <select value={item.is_active ? "active" : "inactive"} disabled={busy || item.id === user.id || item.is_department_admin} onChange={(event) => updateUserActive(item, event.target.value === "active")} style={{ padding: "4px 8px", borderRadius: 4, border: "1px solid var(--color-border)", fontSize: 12, fontFamily: "inherit" }}>
                      <option value="active">启用</option>
                      <option value="inactive">停用</option>
                    </select>
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      disabled={busy || item.id === user.id || item.is_department_admin}
                      onClick={() => setConfirmDeleteUser(item)}
                    >
                      删除
                    </Button>
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
              <select value={departmentAdminUserId} disabled={busy} onChange={(event) => setDepartmentAdminUserId(event.target.value)} style={{ padding: "8px", borderRadius: 4, border: "1px solid var(--color-border)", fontFamily: "inherit" }}>
                <option value="">选择部门管理员</option>
                {availableNewDepartmentAdmins.map((item) => <option key={item.id} value={item.id}>{item.username}（{item.email}）</option>)}
              </select>
              <Button type="submit" size="sm" disabled={busy || !departmentName.trim() || !departmentAdminUserId}>创建部门</Button>
            </form>
            {availableNewDepartmentAdmins.length === 0 && (
              <p className={page.muted}>没有可用管理员候选人。请先在“用户”页创建账号，或将一个有效账号设为未分配部门。</p>
            )}
            <div className={page.cardList}>
              {departments.length === 0 && <p className={page.empty}>暂无部门。</p>}
              {departments.map((department) => (
                <div key={department.id} className={page.card} style={{ padding: "12px 16px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <div>
                    <strong>{department.name}</strong>
                    <span style={{ marginLeft: 12, fontSize: 13, color: "var(--color-text-muted)" }}>{department.description || "暂无说明"}</span>
                    <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--color-text-muted)" }}>
                      当前管理员：{department.admin_username || "尚未设置"}
                    </p>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <select
                      value={department.admin_user_id || ""}
                      disabled={busy || department.admin_user_id === user.id}
                      onChange={(event) => void handleUpdateDepartmentAdmin(department, event.target.value)}
                      style={{ padding: "6px 8px", borderRadius: 4, border: "1px solid var(--color-border)", fontFamily: "inherit" }}
                    >
                      <option value="" disabled>选择管理员</option>
                      {departmentAdminCandidates(department).map((item) => (
                        <option key={item.id} value={item.id}>{item.username}（{item.email}）</option>
                      ))}
                    </select>
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      disabled={busy || department.admin_user_id === user.id}
                      onClick={() => setConfirmDeleteDepartment(department)}
                    >
                      删除
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

      </section>
      {confirmDeleteUser && (
        <ConfirmDialog
          title="删除账号"
          message={`确定永久删除 ${confirmDeleteUser.username}（${confirmDeleteUser.email}）？该账号的会话、私人知识库和记忆会被删除，外部文件与向量会在后台清理；其公共或部门知识库会转交给对应管理员。`}
          confirmLabel="永久删除"
          cancelLabel="取消"
          onConfirm={() => {
            const target = confirmDeleteUser;
            setConfirmDeleteUser(null);
            void handleDeleteUser(target);
          }}
          onCancel={() => setConfirmDeleteUser(null)}
        />
      )}
      {confirmDeleteDepartment && (
        <ConfirmDialog
          title="删除部门"
          message={`确定删除“${confirmDeleteDepartment.name}”吗？仅在部门没有其他成员和部门知识库时可以删除；原部门管理员会恢复为未分配部门。`}
          confirmLabel="删除部门"
          cancelLabel="取消"
          onConfirm={() => {
            const target = confirmDeleteDepartment;
            setConfirmDeleteDepartment(null);
            void handleDeleteDepartment(target);
          }}
          onCancel={() => setConfirmDeleteDepartment(null)}
        />
      )}
    </AppShell>
  );
}
