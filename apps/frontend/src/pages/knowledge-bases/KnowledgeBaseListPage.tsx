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
  return user.department_id ? "department" : "private";
}

function visibilityLabel(kb: KnowledgeBase): string {
  if (kb.visibility === "department") return `${kb.department_name ?? "Department"} / ${kb.role}`;
  if (kb.visibility === "public") return `Organization-wide / ${kb.role}`;
  return `Private / ${kb.role}`;
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
        if (active) setError(err instanceof Error ? err.message : "Knowledge bases could not be loaded.");
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
      setError("Enter a knowledge base name.");
      return;
    }
    if (visibility === "department" && !(user.is_admin ? departmentId : user.department_id)) {
      setError("Select a department before creating a department knowledge base.");
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
      setError(err instanceof Error ? err.message : "Knowledge base could not be created.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: string) {
    const item = items.find((knowledgeBase) => knowledgeBase.id === id);
    if (item?.role !== "owner") {
      setError("Only the owner can delete this knowledge base.");
      return;
    }
    setError("");
    try {
      await deleteKnowledgeBase(token, id);
      setItems((previous) => previous.filter((knowledgeBase) => knowledgeBase.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Knowledge base could not be deleted.");
    }
  }

  const visibilityOptions: { value: KnowledgeBaseVisibility; label: string; disabled?: boolean }[] = [
    { value: "private", label: "Private: only members can access" },
    {
      value: "department",
      label: "Department: visible to the selected department",
      disabled: !user.department_id && departments.length === 0,
    },
    { value: "public", label: "Organization-wide: visible to all users", disabled: !user.is_admin },
  ];

  return (
    <AppShell>
      <TopBar user={{ username: user.username, is_admin: user.is_admin }} onLogout={onLogout} />
      <section className={page.splitPage}>
        <aside className={page.sidePanel}>
          <h2 className={page.sectionTitle}>Create Knowledge Base</h2>
          <p className={page.subtitle} style={{ marginBottom: 16 }}>
            Choose the narrowest useful visibility. Private spaces are isolated to members, department spaces are
            shared within one department, and organization-wide spaces are available to everyone.
          </p>
          <form onSubmit={handleCreate} className={page.formStack}>
            <Input label="Name" value={name} onChange={(event) => setName(event.target.value)} maxLength={120} />
            <Textarea
              label="Description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={4}
            />

            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ fontSize: 13, fontWeight: 500, color: "var(--color-text-secondary)" }}>Visibility</label>
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
                  Department
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
                    value={user.department_name ?? "No department assigned"}
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
              {submitting ? "Creating..." : "Create"}
            </Button>
          </form>
        </aside>

        <section className={page.mainPanel}>
          <div className={page.toolbar}>
            <h2 className={page.sectionTitle}>Knowledge Bases</h2>
            <span className={page.muted}>{items.length} total</span>
          </div>
          {loading && <p className={page.muted}>Loading knowledge bases...</p>}
          {!loading && items.length === 0 && (
            <div className={page.empty}>
              <h3 style={{ color: "var(--color-text)" }}>No accessible knowledge bases</h3>
              <p>
                {user.is_admin
                  ? "Create an organization-wide knowledge base, then upload documents."
                  : "Create a private knowledge base, or wait for an administrator to publish shared content."}
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
                    {knowledgeBase.description || "No description"}
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
                    Delete
                  </Button>
                )}
              </div>
            ))}
          </div>
        </section>
      </section>

      {confirmDeleteId && (
        <ConfirmDialog
          title="Delete knowledge base"
          message="This permanently deletes the knowledge base and all of its documents. This action cannot be undone."
          confirmLabel="Delete"
          cancelLabel="Cancel"
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
