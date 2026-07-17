import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  listDepartments,
  listKnowledgeBases,
  type Department,
  type KnowledgeBase,
  type KnowledgeBaseVisibility,
  type User,
} from "../../lib/api";
import { AppShell } from "../../components/layout/AppShell";
import { TopBar } from "../../components/layout/TopBar";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Textarea } from "../../components/ui/Textarea";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import page from "../../styles/workspace.module.css";

type Props = { token: string; user: User; onLogout: () => Promise<void> };

const selectStyle = {
  padding: "8px 12px",
  borderRadius: 6,
  border: "1px solid var(--color-border)",
  fontSize: 14,
  fontFamily: "inherit",
  background: "var(--color-surface)",
};

function defaultVisibility(user: User): KnowledgeBaseVisibility {
  if (user.is_admin) return "public";
  return user.is_department_admin ? "department" : "private";
}

function visibilityLabel(kb: KnowledgeBase): string {
  const role = kb.role === "owner" ? "所有者" : kb.role === "editor" ? "可编辑" : "只读";
  if (kb.visibility === "department") return `${kb.department_name ?? "部门"} / ${role}`;
  if (kb.visibility === "public") return `全公司 / ${role}`;
  return `私有 / ${role}`;
}

export function KnowledgeBaseListPage({ token, user, onLogout }: Props) {
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState<KnowledgeBaseVisibility>(defaultVisibility(user));
  const [departmentId, setDepartmentId] = useState(user.department_id ?? "");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([listKnowledgeBases(token), listDepartments(token)])
      .then(([knowledgeBases, departmentRows]) => {
        if (!active) return;
        setItems(knowledgeBases);
        setDepartments(departmentRows);
        setDepartmentId((current) => current || user.department_id || departmentRows[0]?.id || "");
        setError("");
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "知识库加载失败。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [token, user]);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!name.trim()) {
      setError("请输入知识库名称。");
      return;
    }
    if (visibility === "department" && !user.is_admin && !user.is_department_admin) {
      setError("只有部门管理员可以创建部门知识库。");
      return;
    }
    if (visibility === "department" && !(user.is_admin ? departmentId : user.department_id)) {
      setError("创建部门知识库前，请先选择部门。");
      return;
    }

    setSubmitting(true);
    try {
      const created = await createKnowledgeBase(token, {
        name: name.trim(),
        description: description.trim(),
        visibility,
        department_id: visibility === "department" ? (user.is_admin ? departmentId : user.department_id) : null,
      });
      setItems((previous) => [created, ...previous]);
      setName("");
      setDescription("");
      setVisibility(defaultVisibility(user));
    } catch (err) {
      setError(err instanceof Error ? err.message : "知识库创建失败。");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: string) {
    const item = items.find((knowledgeBase) => knowledgeBase.id === id);
    if (item?.role !== "owner") {
      setError("只有所有者可以删除此知识库。");
      return;
    }
    setError("");
    try {
      await deleteKnowledgeBase(token, id);
      setItems((previous) => previous.filter((knowledgeBase) => knowledgeBase.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "知识库删除失败。");
    }
  }

  const visibilityOptions: { value: KnowledgeBaseVisibility; label: string; disabled?: boolean }[] = [
    { value: "private", label: "私有：仅成员可访问" },
    {
      value: "department",
      label: "部门：仅部门管理员可维护",
      disabled: !user.is_admin && !user.is_department_admin,
    },
    { value: "public", label: "全公司：所有用户可见", disabled: !user.is_admin },
  ];

  return (
    <AppShell>
      <TopBar user={{ username: user.username, is_admin: user.is_admin }} onLogout={onLogout} />
      <section className={page.splitPage}>
        <aside className={page.sidePanel}>
          <h2 className={page.sectionTitle}>创建知识库</h2>
          <p className={page.subtitle} style={{ marginBottom: 16 }}>
            私有知识库由创建者维护；部门知识库仅部门管理员可维护；全公司知识库仅系统管理员可维护。
          </p>
          <form onSubmit={handleCreate} className={page.formStack}>
            <Input label="名称" value={name} onChange={(event) => setName(event.target.value)} maxLength={120} />
            <Textarea
              label="描述"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={4}
            />

            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ fontSize: 13, fontWeight: 500, color: "var(--color-text-secondary)" }}>可见范围</label>
              <select
                value={visibility}
                onChange={(event) => setVisibility(event.target.value as KnowledgeBaseVisibility)}
                style={selectStyle}
              >
                {visibilityOptions.map((option) => (
                  <option key={option.value} value={option.value} disabled={option.disabled}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            {visibility === "department" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 13, fontWeight: 500, color: "var(--color-text-secondary)" }}>
                  部门
                </label>
                {user.is_admin ? (
                  <select
                    value={departmentId}
                    onChange={(event) => setDepartmentId(event.target.value)}
                    style={selectStyle}
                  >
                    {departments.map((department) => (
                      <option key={department.id} value={department.id}>
                        {department.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={user.department_name ?? "尚未分配部门"}
                    readOnly
                    style={{
                      ...selectStyle,
                      background: "var(--color-surface-muted)",
                      color: "var(--color-text-secondary)",
                    }}
                  />
                )}
              </div>
            )}

            {error && <p className={page.error}>{error}</p>}
            <Button type="submit" disabled={submitting}>
              {submitting ? "创建中..." : "创建"}
            </Button>
          </form>
        </aside>

        <section className={page.mainPanel}>
          <div className={page.toolbar}>
            <h2 className={page.sectionTitle}>知识库</h2>
            <span className={page.muted}>共 {items.length} 个</span>
          </div>
          {loading && <p className={page.muted}>正在加载知识库...</p>}
          {!loading && items.length === 0 && (
            <div className={page.empty}>
              <h3 style={{ color: "var(--color-text)" }}>暂无可访问的知识库</h3>
              <p>
                {user.is_admin
                  ? "创建全公司知识库后，即可上传文档。"
                  : user.is_department_admin
                    ? "你可以创建并维护本部门知识库。"
                    : "你可以创建私有知识库，或使用管理员发布的共享内容。"}
              </p>
            </div>
          )}
          <div className={page.cardList}>
            {items.map((knowledgeBase) => (
              <div
                key={knowledgeBase.id}
                className={page.card}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  flexWrap: "wrap",
                  gap: 14,
                  padding: "14px 16px",
                }}
              >
                <div>
                  <h3 style={{ margin: 0, fontSize: 15 }}>
                    <Link to={`/knowledge-bases/${knowledgeBase.id}`}>{knowledgeBase.name}</Link>
                  </h3>
                  <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--color-text-muted)" }}>
                    {knowledgeBase.description || "暂无描述"}
                  </p>
                  <span
                    style={{
                      display: "inline-block",
                      marginTop: 6,
                      padding: "2px 10px",
                      fontSize: 12,
                      borderRadius: 999,
                      background: "var(--color-primary-soft)",
                      color: "var(--color-primary-strong)",
                      fontWeight: 500,
                    }}
                  >
                    {visibilityLabel(knowledgeBase)}
                  </span>
                </div>
                {knowledgeBase.role === "owner" && (
                  <Button variant="danger" size="sm" onClick={() => setConfirmDeleteId(knowledgeBase.id)}>
                    删除
                  </Button>
                )}
              </div>
            ))}
          </div>
        </section>
      </section>

      {confirmDeleteId && (
        <ConfirmDialog
          title="删除知识库"
          message="此操作将永久删除该知识库及其全部文档，且无法撤销。"
          confirmLabel="删除"
          cancelLabel="取消"
          onConfirm={() => {
            void handleDelete(confirmDeleteId);
            setConfirmDeleteId(null);
          }}
          onCancel={() => setConfirmDeleteId(null)}
        />
      )}
    </AppShell>
  );
}
