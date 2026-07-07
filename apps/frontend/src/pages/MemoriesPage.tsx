import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  createUserMemory,
  deleteUserMemory,
  listUserMemories,
  updateUserMemory,
  type User,
  type UserMemory,
} from "../lib/api";

type Props = {
  token: string;
  user: User;
  onLogout: () => Promise<void>;
};

const statusOptions = ["active", "pending", "superseded", "ignored"] as const;

export function MemoriesPage({ token, user, onLogout }: Props) {
  const navigate = useNavigate();
  const [memories, setMemories] = useState<UserMemory[]>([]);
  const [statusFilter, setStatusFilter] = useState("active");
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("general");
  const [kind, setKind] = useState("preference");
  const [editingId, setEditingId] = useState("");
  const [draft, setDraft] = useState<MemoryDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const activeCount = useMemo(() => memories.filter((memory) => memory.status === "active").length, [memories]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    listUserMemories(token, statusFilter || undefined)
      .then((items) => {
        if (active) {
          setMemories(items);
          setError("");
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "记忆加载失败。");
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
  }, [statusFilter, token]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = content.trim();
    if (!trimmed) {
      setError("请输入记忆内容。");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const created = await createUserMemory(token, {
        content: trimmed,
        category: category.trim() || "general",
        kind: kind.trim() || "preference",
      });
      setMemories((current) => [created, ...current.filter((memory) => memory.id !== created.id)]);
      setContent("");
      setCategory("general");
      setKind("preference");
    } catch (err) {
      setError(err instanceof Error ? err.message : "记忆保存失败。");
    } finally {
      setSaving(false);
    }
  }

  function startEdit(memory: UserMemory) {
    setEditingId(memory.id);
    setDraft({
      content: memory.content,
      category: memory.category,
      kind: memory.kind,
      status: memory.status,
    });
  }

  async function saveEdit(memoryId: string) {
    if (!draft || !draft.content.trim()) {
      setError("请输入记忆内容。");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const updated = await updateUserMemory(token, memoryId, {
        content: draft.content.trim(),
        category: draft.category.trim() || "general",
        kind: draft.kind.trim() || "preference",
        status: draft.status,
      });
      setMemories((current) => current.map((memory) => (memory.id === updated.id ? updated : memory)));
      setEditingId("");
      setDraft(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "记忆更新失败。");
    } finally {
      setSaving(false);
    }
  }

  async function removeMemory(memoryId: string) {
    setError("");
    try {
      await deleteUserMemory(token, memoryId);
      setMemories((current) => current.filter((memory) => memory.id !== memoryId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "记忆删除失败。");
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
          <p className="eyebrow">Memory</p>
          <h1>长期记忆</h1>
        </div>
        <div className="topbar-actions">
          <Link to="/chat">问答</Link>
          {user.is_admin && <Link to="/admin">管理员控制台</Link>}
          <span>{user.username}</span>
          <button type="button" className="secondary-button" onClick={submitLogout}>
            退出
          </button>
        </div>
      </header>

      <section className="workspace memories-workspace">
        <aside className="side-panel">
          <h2>添加记忆</h2>
          <form className="form" onSubmit={handleCreate}>
            <label>
              <span>内容</span>
              <textarea value={content} onChange={(event) => setContent(event.target.value)} rows={5} />
            </label>
            <label>
              <span>分类</span>
              <input value={category} onChange={(event) => setCategory(event.target.value)} maxLength={80} />
            </label>
            <label>
              <span>类型</span>
              <input value={kind} onChange={(event) => setKind(event.target.value)} maxLength={40} />
            </label>
            <button type="submit" disabled={saving}>
              {saving ? "保存中" : "保存"}
            </button>
          </form>
          {error && <p className="form-error">{error}</p>}
        </aside>

        <section className="content-panel">
          <div className="section-heading">
            <div>
              <h2>记忆列表</h2>
              <p>当前筛选 {memories.length} 条，active {activeCount} 条</p>
            </div>
            <label className="inline-filter">
              <span>状态</span>
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="">全部</option>
                {statusOptions.map((status) => (
                  <option value={status} key={status}>
                    {status}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {loading && <p className="muted">正在加载记忆。</p>}
          {!loading && memories.length === 0 && (
            <div className="empty-state">
              <h3>暂无记忆</h3>
              <p>添加稳定偏好、背景或任务结论后，问答会自动带入上下文。</p>
            </div>
          )}

          <div className="memory-table-list">
            {memories.map((memory) => (
              <article className={`memory-row ${memory.status}`} key={memory.id}>
                {editingId === memory.id && draft ? (
                  <EditMemoryForm
                    draft={draft}
                    onDraftChange={setDraft}
                    onCancel={() => {
                      setEditingId("");
                      setDraft(null);
                    }}
                    onSave={() => void saveEdit(memory.id)}
                    saving={saving}
                  />
                ) : (
                  <>
                    <div>
                      <div className="memory-row-meta">
                        <span className={`status-pill ${memory.status}`}>{memory.status}</span>
                        <span>{memory.category}</span>
                        <span>{memory.kind}</span>
                      </div>
                      <p>{memory.content}</p>
                      <small>
                        touched {memory.touched_count} · merged {memory.merge_count} · updated{" "}
                        {new Date(memory.updated_at).toLocaleString()}
                      </small>
                    </div>
                    <div className="document-actions">
                      <button type="button" className="secondary-button compact-button" onClick={() => startEdit(memory)}>
                        编辑
                      </button>
                      <button
                        type="button"
                        className="danger-button compact-button"
                        onClick={() => void removeMemory(memory.id)}
                      >
                        删除
                      </button>
                    </div>
                  </>
                )}
              </article>
            ))}
          </div>
        </section>
      </section>
    </main>
  );
}

type MemoryDraft = {
  content: string;
  category: string;
  kind: string;
  status: string;
};

function EditMemoryForm({
  draft,
  onDraftChange,
  onCancel,
  onSave,
  saving,
}: {
  draft: MemoryDraft;
  onDraftChange: (draft: MemoryDraft) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
}) {
  return (
    <div className="memory-edit-form">
      <label>
        <span>内容</span>
        <textarea
          value={draft.content}
          onChange={(event) => onDraftChange({ ...draft, content: event.target.value })}
          rows={4}
        />
      </label>
      <div className="memory-edit-grid">
        <label>
          <span>分类</span>
          <input
            value={draft.category}
            onChange={(event) => onDraftChange({ ...draft, category: event.target.value })}
            maxLength={80}
          />
        </label>
        <label>
          <span>类型</span>
          <input value={draft.kind} onChange={(event) => onDraftChange({ ...draft, kind: event.target.value })} />
        </label>
        <label>
          <span>状态</span>
          <select value={draft.status} onChange={(event) => onDraftChange({ ...draft, status: event.target.value })}>
            {statusOptions.map((status) => (
              <option value={status} key={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="document-actions">
        <button type="button" onClick={onSave} disabled={saving}>
          保存
        </button>
        <button type="button" className="secondary-button" onClick={onCancel} disabled={saving}>
          取消
        </button>
      </div>
    </div>
  );
}
