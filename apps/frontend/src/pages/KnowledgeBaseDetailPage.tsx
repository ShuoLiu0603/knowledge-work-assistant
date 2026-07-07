import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  askKnowledgeBase,
  deleteDocument,
  deleteKnowledgeBase,
  getKnowledgeBase,
  listDocumentChunks,
  listDocuments,
  uploadDocument,
  type AskKnowledgeBaseResult,
  type DocumentChunk,
  type DocumentItem,
  type KnowledgeBase,
  type User,
} from "../lib/api";

type Props = {
  token: string;
  user: User;
};

export function KnowledgeBaseDetailPage({ token, user }: Props) {
  const navigate = useNavigate();
  const { id } = useParams();
  const [item, setItem] = useState<KnowledgeBase | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedChunks, setSelectedChunks] = useState<DocumentChunk[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [question, setQuestion] = useState("");
  const [qaResult, setQaResult] = useState<AskKnowledgeBaseResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const [uploadSecurityLevel, setUploadSecurityLevel] = useState(1);

  useEffect(() => {
    if (!id) {
      setError("缺少知识库 ID。");
      setLoading(false);
      return;
    }

    let active = true;
    Promise.all([getKnowledgeBase(token, id), listDocuments(token, id)])
      .then(([data, docs]) => {
        if (active) {
          setItem(data);
          setDocuments(docs);
          setError("");
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "知识库加载失败。");
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
  }, [id, token]);

  async function refreshDocuments() {
    if (!id) {
      return;
    }
    setDocumentsLoading(true);
    try {
      const docs = await listDocuments(token, id);
      setDocuments(docs);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "文档列表加载失败。");
    } finally {
      setDocumentsLoading(false);
    }
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    if (!canManageData) {
      setError("只有 owner 或 editor 可以上传文档。");
      return;
    }
    if (!id || !event.target.files?.[0]) {
      return;
    }

    setUploading(true);
    setError("");
    try {
      await uploadDocument(token, id, event.target.files[0], item?.visibility !== "private" ? uploadSecurityLevel : 1);
      event.target.value = "";
      await refreshDocuments();
    } catch (err) {
      setError(err instanceof Error ? err.message : "文档上传失败。");
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteDocument(documentId: string) {
    if (!canManageData) {
      setError("只有 owner 或 editor 可以删除文档。");
      return;
    }
    setError("");
    try {
      await deleteDocument(token, documentId);
      setDocuments((current) => current.filter((doc) => doc.id !== documentId));
      if (selectedDocumentId === documentId) {
        setSelectedDocumentId("");
        setSelectedChunks([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除文档失败。");
    }
  }

  async function handleShowChunks(documentId: string) {
    setError("");
    try {
      const chunks = await listDocumentChunks(token, documentId);
      setSelectedDocumentId(documentId);
      setSelectedChunks(chunks);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chunk 加载失败。");
    }
  }

  async function handleAsk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!id) {
      return;
    }

    const trimmed = question.trim();
    if (!trimmed) {
      setError("请输入问题。");
      return;
    }

    setAsking(true);
    setError("");
    try {
      const result = await askKnowledgeBase(token, id, { question: trimmed });
      setQaResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "问答请求失败。");
    } finally {
      setAsking(false);
    }
  }

  async function handleDelete() {
    if (!canDeleteKnowledgeBase) {
      setError("只有 owner 可以删除知识库。");
      return;
    }
    if (!item) {
      return;
    }

    setError("");
    try {
      await deleteKnowledgeBase(token, item.id);
      navigate("/knowledge-bases", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除知识库失败。");
    }
  }

  const hasIndexedDocument = documents.some((document) => document.status === "indexed" && document.chunk_count > 0);
  const canManageData = item?.role === "owner" || item?.role === "editor";
  const canDeleteKnowledgeBase = item?.role === "owner";
  const showsSecurityLevel = item?.visibility !== "private";
  const uploadSecurityLevels = Array.from(
    { length: user.is_admin ? 5 : Math.max(1, user.security_level) },
    (_, index) => index + 1,
  );

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Knowledge Base</p>
          <h1>知识库详情</h1>
        </div>
        <div className="topbar-actions">
          <Link to="/chat">问答</Link>
          <Link to="/memories">记忆</Link>
          {user.is_admin && <Link to="/admin">管理员控制台</Link>}
          <Link to="/knowledge-bases">返回列表</Link>
        </div>
      </header>

      <section className="detail-panel">
        {loading && <p className="muted">正在加载知识库。</p>}
        {error && <p className="form-error">{error}</p>}

        {item && (
          <>
            <div className="section-heading">
              <div>
                <h2>{item.name}</h2>
                <p>{item.description || "暂无描述"}</p>
              </div>
              <span className="badge">
                {visibilityLabel(item)}
              </span>
            </div>

            <dl className="status-grid">
              <div>
                <dt>Visibility</dt>
                <dd>{visibilityLabel(item)}</dd>
              </div>
              <div>
                <dt>Department</dt>
                <dd>{item.department_name || "-"}</dd>
              </div>
              <div>
                <dt>Owner ID</dt>
                <dd>{item.owner_id}</dd>
              </div>
              <div>
                <dt>Created</dt>
                <dd>{new Date(item.created_at).toLocaleString()}</dd>
              </div>
              <div>
                <dt>Updated</dt>
                <dd>{new Date(item.updated_at).toLocaleString()}</dd>
              </div>
            </dl>

            {canDeleteKnowledgeBase && (
              <div className="actions">
                <button type="button" className="danger-button" onClick={() => void handleDelete()}>
                  删除知识库
                </button>
              </div>
            )}

            <section className="document-section">
              <div className="section-heading">
                <div>
                  <h2>文档</h2>
                  <p>
                    {canManageData
                      ? item.visibility === "private"
                        ? "私人知识库默认仅你可见，上传文档默认全部可读。"
                        : "上传后由 worker 解析、清洗、切分、向量化，并按 L1-L5 清级控制可见范围。"
                      : item.visibility === "public"
                        ? "公开知识库由管理员维护文档；你可以检索自己等级可访问的内容。"
                        : item.visibility === "department"
                          ? "部门知识库对同部门开放检索，文档维护由 owner/editor 或管理员负责。"
                          : "私人知识库按成员权限隔离。"}
                  </p>
                </div>
                <button type="button" className="secondary-button" onClick={() => void refreshDocuments()}>
                  {documentsLoading ? "刷新中" : "刷新状态"}
                </button>
              </div>

              {canManageData && (
                <label className="upload-box">
                  <span>{uploading ? "上传中" : "上传文档"}</span>
                  {showsSecurityLevel && (
                    <select
                      value={uploadSecurityLevel}
                      onChange={(event) => setUploadSecurityLevel(Number(event.target.value))}
                      disabled={uploading}
                    >
                      {uploadSecurityLevels.map((level) => (
                        <option value={level} key={level}>
                          L{level} 密级
                        </option>
                      ))}
                    </select>
                  )}
                  <input
                    type="file"
                    accept=".pdf,.docx,.txt,.md,.csv"
                    disabled={uploading}
                    onChange={(event) => void handleUpload(event)}
                  />
                </label>
              )}

              {documents.length === 0 && (
                <p className="muted">
                  {canManageData ? "还没有上传文档。" : "暂无你当前等级可见的文档。"}
                </p>
              )}

              <div className="document-list">
                {documents.map((document) => (
                  <article className="document-item" key={document.id}>
                    <div>
                      <h3>{document.file_name}</h3>
                      <p>
                        {formatBytes(document.file_size)} · {document.file_ext.toUpperCase()} · chunks:{" "}
                        {document.chunk_count}
                        {showsSecurityLevel ? ` · L${document.security_level}` : ""}
                      </p>
                      {document.error_message && <p className="inline-error">{document.error_message}</p>}
                    </div>
                    <div className="document-actions">
                      <span className={`status-pill ${document.status}`}>{document.status}</span>
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={document.chunk_count === 0}
                        onClick={() => void handleShowChunks(document.id)}
                      >
                        Chunks
                      </button>
                      {canManageData && (
                        <button
                          type="button"
                          className="danger-button"
                          onClick={() => void handleDeleteDocument(document.id)}
                        >
                          删除
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>

              {selectedDocumentId && (
                <div className="chunks-panel">
                  <h3>Chunks</h3>
                  {selectedChunks.length === 0 && <p className="muted">暂无 chunk。</p>}
                  {selectedChunks.slice(0, 8).map((chunk) => (
                    <article className="chunk-item" key={chunk.id}>
                      <strong>#{chunk.chunk_index}</strong>
                      <p>{chunk.content}</p>
                      <small>
                        {showsSecurityLevel ? `L${chunk.security_level} · ` : ""}tokens: {chunk.token_count}
                        {chunk.page_number ? ` · page ${chunk.page_number}` : ""}
                        {chunk.title_path ? ` · ${chunk.title_path}` : ""}
                      </small>
                    </article>
                  ))}
                </div>
              )}
            </section>

            <section className="qa-section">
              <div className="section-heading">
                <div>
                  <h2>问答</h2>
                  <p>答案基于 indexed 文档的向量检索结果生成。</p>
                </div>
              </div>

              <form className="qa-form" onSubmit={(event) => void handleAsk(event)}>
                <label>
                  <span>问题</span>
                  <textarea
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    rows={3}
                    placeholder="例如：报销流程是什么？"
                  />
                </label>
                <button type="submit" disabled={asking || !hasIndexedDocument}>
                  {asking ? "检索中" : "提问"}
                </button>
              </form>

              {!hasIndexedDocument && (
                <p className="muted">
                  {canManageData
                    ? "请先上传文档，并等待状态变为 indexed。"
                    : "当前没有已入库且你可访问的文档，可联系管理员确认数据源和文档清级。"}
                </p>
              )}

              {qaResult && (
                <div className="answer-panel">
                  <h3>回答</h3>
                  <p>{qaResult.answer}</p>

                  <h3>引用</h3>
                  {qaResult.citations.length === 0 && <p className="muted">暂无引用。</p>}
                  <div className="citation-list">
                    {qaResult.citations.map((citation, index) => (
                      <article className="citation-item" key={citation.chunk_id}>
                        <strong>
                          [{index + 1}] {citation.file_name} · chunk #{citation.chunk_index}
                        </strong>
                        <p>{citation.content_preview}</p>
                        <small>
                          {showsSecurityLevel ? `L${citation.security_level} · ` : ""}score: {citation.score}
                          {citation.page_number ? ` · page ${citation.page_number}` : ""}
                          {citation.title_path ? ` · ${citation.title_path}` : ""}
                        </small>
                      </article>
                    ))}
                  </div>
                </div>
              )}
            </section>
          </>
        )}
      </section>
    </main>
  );
}

function visibilityLabel(item: KnowledgeBase): string {
  if (item.visibility === "department") {
    return `${item.department_name || "部门"} · ${item.role}`;
  }
  if (item.visibility === "public") {
    return `公司公开 · ${item.role}`;
  }
  return `私有 · ${item.role}`;
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
