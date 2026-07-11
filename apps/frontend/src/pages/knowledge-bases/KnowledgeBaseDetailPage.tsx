import { ChangeEvent, FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  askKnowledgeBase, deleteDocument, deleteKnowledgeBase, getKnowledgeBase,
  listDocumentChunks, listDocuments, uploadDocument,
  type DocumentChunk, type DocumentItem, type KnowledgeBase, type User, type AskKnowledgeBaseResult,
} from "../../lib/api";
import { AppShell } from "../../components/layout/AppShell";
import { TopBar } from "../../components/layout/TopBar";
import { Button } from "../../components/ui/Button";
import { StatusPill } from "../../components/ui/StatusPill";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import page from "../../styles/workspace.module.css";

type Props = { token: string; user: User; onLogout: () => Promise<void> };

function visStr(kb: KnowledgeBase): string {
  const role = kb.role === "owner" ? "所有者" : kb.role === "editor" ? "可编辑" : "只读";
  if (kb.visibility === "department") return `${kb.department_name ?? "部门"} · ${role}`;
  if (kb.visibility === "public") return `全公司 · ${role}`;
  return `私有 · ${role}`;
}

function documentStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "等待处理",
    processing: "处理中",
    indexed: "已入库",
    failed: "处理失败",
  };
  return labels[status] ?? status;
}

function fmtBytes(v: number): string {
  if (v < 1024) return `${v} B`;
  if (v < 1024*1024) return `${(v/1024).toFixed(1)} KB`;
  return `${(v/1024/1024).toFixed(1)} MB`;
}

export function KnowledgeBaseDetailPage({ token, user, onLogout }: Props) {
  const { id } = useParams();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [selectedDocId, setSelectedDocId] = useState("");
  const [question, setQuestion] = useState("");
  const [qaResult, setQaResult] = useState<AskKnowledgeBaseResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [asking, setAsking] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [upSL, setUpSL] = useState(1);
  const [confirmDelDoc, setConfirmDelDoc] = useState<string | null>(null);
  const [confirmDelKb, setConfirmDelKb] = useState(false);

  useEffect(() => {
    if (!id) { setError("缺少知识库 ID。"); setLoading(false); return; }
    let active = true;
    Promise.all([getKnowledgeBase(token, id), listDocuments(token, id)])
      .then(([k, d]) => { if (active) { setKb(k); setDocs(d); setError(""); } })
      .catch((e) => { if (active) setError(e instanceof Error ? e.message : "加载失败。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id, token]);

  async function refreshDocs() {
    if (!id) return;
    try { setDocs(await listDocuments(token, id)); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "刷新失败。"); }
  }

  async function handleUpload(e: ChangeEvent<HTMLInputElement>) {
    if (!e.target.files?.[0]) return;
    if (!canManage || !id) {
      setError("当前账号没有上传权限。");
      e.target.value = "";
      return;
    }
    setUploading(true); setError("");
    try {
      await uploadDocument(token, id, e.target.files[0], kb?.visibility !== "private" ? upSL : 1);
      e.target.value = "";
      await refreshDocs();
    } catch (e) { setError(e instanceof Error ? e.message : "上传失败。"); }
    finally { setUploading(false); }
  }

  function openFilePicker() {
    if (!uploading && canManage) fileInputRef.current?.click();
  }

  function handleUploadKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    openFilePicker();
  }

  async function handleDelDoc(docId: string) {
    if (!canManage) return;
    setError("");
    try {
      await deleteDocument(token, docId);
      setDocs((p) => p.filter((d) => d.id !== docId));
      if (selectedDocId === docId) { setSelectedDocId(""); setChunks([]); }
    } catch (e) { setError(e instanceof Error ? e.message : "删除失败。"); }
  }

  function confirmDelDocFn(docId: string) {
    setConfirmDelDoc(docId);
  }

  async function showChunks(docId: string) {
    setError("");
    try { setSelectedDocId(docId); setChunks(await listDocumentChunks(token, docId)); }
    catch (e) { setError(e instanceof Error ? e.message : "加载分段失败。"); }
  }

  async function handleAsk(e: FormEvent) {
    e.preventDefault();
    if (!id || !question.trim()) return;
    setAsking(true); setError("");
    try { setQaResult(await askKnowledgeBase(token, id, { question: question.trim() })); }
    catch (e) { setError(e instanceof Error ? e.message : "问答失败。"); }
    finally { setAsking(false); }
  }

  async function handleDelKb() {
    if (!kb || kb.role !== "owner") return setError("只有所有者可以删除。");
    setError("");
    try { await deleteKnowledgeBase(token, kb.id); window.location.href = "/knowledge-bases"; }
    catch (e) { setError(e instanceof Error ? e.message : "删除失败。"); }
  }

  const canManage = kb?.role === "owner" || kb?.role === "editor";
  const hasIndexed = docs.some((d) => d.status === "indexed" && d.chunk_count > 0);
  const showsSL = kb?.visibility !== "private";
  const slLevels = Array.from({ length: user.is_admin ? 5 : Math.max(1, user.security_level) }, (_, i) => i + 1);

  return (
    <AppShell>
      <TopBar user={{ username: user.username, is_admin: user.is_admin }} onLogout={onLogout} />
      <section className={`${page.page} ${page.narrowPage}`}>
        {loading && <p className={page.muted}>正在加载...</p>}
        {error && <p className={page.error}>{error}</p>}
        {!kb && !loading && <p>知识库未找到。</p>}

        {kb && (
          <>
            <div className={page.pageHeader}>
              <div>
                <h1 className={page.title}>{kb.name}</h1>
                <p className={page.subtitle}>{kb.description || "暂无描述"}</p>
              </div>
              <span style={{ display: "inline-block", marginTop: 8, padding: "2px 10px", fontSize: 12, borderRadius: 999, background: "var(--color-primary-soft)", color: "var(--color-primary-strong)", fontWeight: 500 }}>{visStr(kb)}</span>
            </div>

            <dl className={page.statsGrid} style={{ fontSize: 13 }}>
              <div><dt style={{ color: "var(--color-text-muted)", fontSize: 11 }}>部门</dt><dd style={{ margin: 0 }}>{kb.department_name || "-"}</dd></div>
              <div><dt style={{ color: "var(--color-text-muted)", fontSize: 11 }}>创建</dt><dd style={{ margin: 0 }}>{new Date(kb.created_at).toLocaleString()}</dd></div>
              <div><dt style={{ color: "var(--color-text-muted)", fontSize: 11 }}>更新</dt><dd style={{ margin: 0 }}>{new Date(kb.updated_at).toLocaleString()}</dd></div>
            </dl>

            {kb.role === "owner" && <Button variant="danger" size="sm" onClick={() => setConfirmDelKb(true)} style={{ marginBottom: 24 }}>删除知识库</Button>}

            {/* documents */}
            <section style={{ marginBottom: 24 }}>
              <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>文档</h2>

              {canManage && (
                <div
                  className={`${page.uploadBox} ${uploading ? page.uploadBoxBusy : ""}`}
                  role="button"
                  tabIndex={uploading ? -1 : 0}
                  onClick={openFilePicker}
                  onKeyDown={handleUploadKeyDown}
                  aria-disabled={uploading}
                >
                  <div>
                    <span className={page.uploadTitle}>{uploading ? "上传中..." : "选择文件并上传"}</span>
                    <p className={page.uploadHint}>支持 PDF、DOCX、TXT、Markdown、CSV。选择后会立即上传并刷新文档列表。</p>
                  </div>
                  {showsSL && (
                    <label className={page.uploadControl} onClick={(e) => e.stopPropagation()}>
                      <span>密级</span>
                      <select value={upSL} onChange={(e) => setUpSL(Number(e.target.value))} disabled={uploading}>
                        {slLevels.map((l) => <option key={l} value={l}>L{l}</option>)}
                      </select>
                    </label>
                  )}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.docx,.txt,.md,.csv"
                    disabled={uploading}
                    onChange={handleUpload}
                    className={page.fileInput}
                  />
                </div>
              )}

              {docs.length === 0 && <p style={{ color: "var(--color-text-muted)", fontSize: 13 }}>暂无文档。</p>}
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {docs.map((d) => (
                  <div key={d.id} className={page.card} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, padding: "12px 14px" }}>
                    <div>
                      <strong style={{ fontSize: 14 }}>{d.file_name}</strong>
                      <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--color-text-muted)" }}>
                        {fmtBytes(d.file_size)} · {d.file_ext.toUpperCase()} · 分段：{d.chunk_count}{showsSL ? ` · L${d.security_level}` : ""}
                        {d.error_message && <span style={{ color: "var(--color-danger)", marginLeft: 8 }}>{d.error_message}</span>}
                      </p>
                    </div>
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <StatusPill variant={d.status} label={documentStatusLabel(d.status)} />
                      <Button variant="ghost" size="sm" disabled={d.chunk_count === 0} onClick={() => showChunks(d.id)}>查看分段</Button>
                      {canManage && <Button variant="danger" size="sm" onClick={() => confirmDelDocFn(d.id)}>删除</Button>}
                    </div>
                  </div>
                ))}
              </div>

              {selectedDocId && chunks.length > 0 && (
                <div style={{ marginTop: 16, padding: 16, borderRadius: 8, border: "1px solid var(--color-border)", background: "var(--color-surface-muted)" }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>文档分段（{chunks.length}）</h3>
                  {chunks.slice(0, 8).map((c) => (
                    <div key={c.id} style={{ padding: "8px 10px", marginBottom: 4, borderRadius: 6, background: "var(--color-surface)", border: "1px solid var(--color-border)", fontSize: 13 }}>
                      <strong>#{c.chunk_index}</strong>
                      <p style={{ margin: "2px 0", color: "var(--color-text-secondary)" }}>{c.content.slice(0, 200)}...</p>
                      <small style={{ color: "var(--color-text-muted)" }}>
                        {showsSL ? `L${c.security_level} · ` : ""}Token 数：{c.token_count}{c.page_number ? ` · 第 ${c.page_number} 页` : ""}
                      </small>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Q&A */}
            <section>
              <h2 style={{ fontSize: 16, margin: "0 0 12px" }}>知识库问答</h2>
              <form onSubmit={handleAsk} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
                <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="例如：报销流程是什么？" style={{ flex: 1, padding: "8px 12px", borderRadius: 6, border: "1px solid var(--color-border)", fontSize: 14, fontFamily: "inherit" }} />
                <Button type="submit" disabled={asking || !hasIndexed}>{asking ? "检索中" : "提问"}</Button>
              </form>

              {!hasIndexed && <p style={{ color: "var(--color-text-muted)", fontSize: 13 }}>请先上传文档等入库。</p>}

              {qaResult && (
                <div className={page.card} style={{ padding: 16 }}>
                  <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>回答</h3>
                  <p style={{ whiteSpace: "pre-wrap", lineHeight: 1.6, fontSize: 14 }}>{qaResult.answer}</p>
                  {qaResult.citations.length > 0 && (
                    <>
                      <h4 style={{ fontSize: 13, margin: "16px 0 8px" }}>引用 ({qaResult.citations.length})</h4>
                      {qaResult.citations.map((c, i) => (
                        <div key={`${c.chunk_id}-${i}`} style={{ padding: "6px 10px", marginBottom: 4, borderRadius: 6, background: "var(--color-surface-muted)", border: "1px solid var(--color-border)", fontSize: 12 }}>
                          <strong>[{i+1}] {c.file_name} · 分段 #{c.chunk_index}</strong>
                          <p style={{ margin: "2px 0", color: "var(--color-text-secondary)" }}>{c.content_preview}</p>
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}
            </section>
          </>
        )}
      </section>

      {confirmDelDoc && (
        <ConfirmDialog
          title="删除文档"
          message="确定要永久删除此文档？文档的向量数据将一并清除，此操作不可撤销。"
          onConfirm={() => { handleDelDoc(confirmDelDoc); setConfirmDelDoc(null); }}
          onCancel={() => setConfirmDelDoc(null)}
        />
      )}
      {confirmDelKb && (
        <ConfirmDialog
          title="删除知识库"
          message="确定要永久删除此知识库及其所有文档？此操作不可撤销。"
          onConfirm={() => { handleDelKb(); setConfirmDelKb(false); }}
          onCancel={() => setConfirmDelKb(false)}
        />
      )}
    </AppShell>
  );
}
