import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  listDepartments,
  listKnowledgeBases,
  type Department,
  type KnowledgeBase,
  type KnowledgeBaseVisibility,
  type User,
} from "../lib/api";

type Props = {
  token: string;
  user: User;
  onLogout: () => Promise<void>;
};

export function KnowledgeBaseListPage({ token, user, onLogout }: Props) {
  const navigate = useNavigate();
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState<KnowledgeBaseVisibility>(user.is_admin ? "public" : user.department_id ? "department" : "private");
  const [departmentId, setDepartmentId] = useState(user.department_id ?? "");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([listKnowledgeBases(token), listDepartments(token)])
      .then(([data, loadedDepartments]) => {
        if (active) {
          setItems(data);
          setDepartments(loadedDepartments);
          setDepartmentId((current) => current || user.department_id || loadedDepartments[0]?.id || "");
          setError("");
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "知识库列表加载失败。");
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

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (!name.trim()) {
      setError("请输入知识库名称。");
      return;
    }
    if (visibility === "department" && !(user.is_admin ? departmentId : user.department_id)) {
      setError("创建部门知识库前需要先设置用户所属部门。");
      return;
    }

    setSubmitting(true);
    try {
      const created = await createKnowledgeBase(token, {
        name,
        description,
        visibility,
        department_id: visibility === "department" ? (user.is_admin ? departmentId : user.department_id) : null,
      });
      setItems((current) => [created, ...current]);
      setName("");
      setDescription("");
      setVisibility(user.is_admin ? "public" : user.department_id ? "department" : "private");
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建知识库失败。");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: string) {
    const target = items.find((item) => item.id === id);
    if (target?.role !== "owner") {
      setError("只有 owner 可以删除知识库。");
      return;
    }
    setError("");
    try {
      await deleteKnowledgeBase(token, id);
      setItems((current) => current.filter((item) => item.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除知识库失败。");
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
          <p className="eyebrow">Knowledge Base</p>
          <h1>知识库</h1>
        </div>
        <div className="topbar-actions">
          <Link to="/chat">问答</Link>
          <Link to="/memories">记忆</Link>
          {user.is_admin && <Link to="/admin">管理员控制台</Link>}
          <span>{user.username}</span>
          <button type="button" className="secondary-button" onClick={submitLogout}>
            退出
          </button>
        </div>
      </header>

      <section className="workspace">
        <aside className="side-panel">
          <h2>创建知识库</h2>
          <p className="summary">
            资料库按组织边界授权：私有库按成员隔离，部门库对同部门可见，公司公开库对全部用户可见。
          </p>
          <form className="form" onSubmit={handleCreate}>
            <label>
              <span>名称</span>
              <input value={name} onChange={(event) => setName(event.target.value)} maxLength={120} />
            </label>
            <label>
              <span>描述</span>
              <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={4} />
            </label>
            <label>
              <span>可见性</span>
              <select value={visibility} onChange={(event) => setVisibility(event.target.value as KnowledgeBaseVisibility)}>
                <option value="private">私有：仅成员可见</option>
                <option value="department" disabled={!user.department_id && departments.length === 0}>
                  部门：同部门可见
                </option>
                <option value="public" disabled={!user.is_admin}>
                  公司公开：全部用户可见
                </option>
              </select>
            </label>
            {visibility === "department" && (
              <label>
                <span>所属部门</span>
                {user.is_admin ? (
                  <select value={departmentId} onChange={(event) => setDepartmentId(event.target.value)}>
                    {departments.map((department) => (
                      <option value={department.id} key={department.id}>
                        {department.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input value={user.department_name || "未设置部门"} readOnly />
                )}
              </label>
            )}
            <button type="submit" disabled={submitting}>
              {submitting ? "创建中" : "创建"}
            </button>
          </form>
          {error && <p className="form-error">{error}</p>}
        </aside>

        <section className="content-panel">
          <div className="section-heading">
            <h2>我的知识库</h2>
            <span>{items.length} 个</span>
          </div>

          {loading && <p className="muted">正在加载知识库。</p>}

          {!loading && items.length === 0 && (
            <div className="empty-state">
              <h3>暂无可访问知识库</h3>
              <p>{user.is_admin ? "先创建一个公开知识库，再上传文档入库。" : "先创建一个私人知识库，或使用管理员发布的公开知识库。"}</p>
            </div>
          )}

          <div className="kb-list">
            {items.map((item) => (
              <article className="kb-item" key={item.id}>
                <div>
                  <h3>
                    <Link to={`/knowledge-bases/${item.id}`}>{item.name}</Link>
                  </h3>
                  <p>{item.description || "暂无描述"}</p>
                  <span className="badge">
                    {visibilityLabel(item)}
                  </span>
                </div>
                {item.role === "owner" && (
                  <button type="button" className="danger-button" onClick={() => void handleDelete(item.id)}>
                    删除
                  </button>
                )}
              </article>
            ))}
          </div>
        </section>
      </section>
    </main>
  );
}

function visibilityLabel(item: KnowledgeBase): string {
  if (item.visibility === "department") {
    return `${item.department_name || "部门库"} · ${item.role}`;
  }
  if (item.visibility === "public") {
    return `公司公开 · ${item.role}`;
  }
  return `私有 · ${item.role}`;
}
