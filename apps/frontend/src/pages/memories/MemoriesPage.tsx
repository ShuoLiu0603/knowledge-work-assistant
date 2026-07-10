import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  approveUserMemory,
  createUserMemory,
  deleteUserMemory,
  exportUserMemoryData,
  listUserMemoryUpdateJobs,
  listUserMemories,
  purgeUserMemory,
  rejectUserMemory,
  retryUserMemoryUpdateJob,
  restoreUserMemory,
  updateUserMemory,
  type User,
  type UserMemory,
  type UserMemoryUpdateJob,
} from "../../lib/api";
import { AppShell } from "../../components/layout/AppShell";
import { TopBar } from "../../components/layout/TopBar";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Input";
import { Textarea } from "../../components/ui/Textarea";
import { StatusPill } from "../../components/ui/StatusPill";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import page from "../../styles/workspace.module.css";
import styles from "./MemoriesPage.module.css";

type Props = { token: string; user: User; onLogout: () => Promise<void> };
type MemoryStatus = UserMemory["status"];
type Draft = {
  content: string;
  category: string;
  kind: string;
  status: MemoryStatus;
  revision: number;
  confirmSensitive: boolean;
};
type ConfirmAction = { kind: "delete" | "purge"; memoryId: string } | null;

const STATUS_OPTS: MemoryStatus[] = ["active", "pending", "superseded", "ignored", "deleted"];
const RECENT_JOB_LIMIT = 8;
const VISIBLE_JOB_STATUSES = new Set<UserMemoryUpdateJob["status"]>(["queued", "processing", "failed"]);

function isLeaseExpired(job: UserMemoryUpdateJob) {
  if (job.status !== "processing" || !job.lease_expires_at) return false;
  const expiresAt = Date.parse(job.lease_expires_at);
  return Number.isFinite(expiresAt) && expiresAt <= Date.now();
}

function formatTimestamp(value: string) {
  return new Date(value).toLocaleString();
}

export function MemoriesPage({ token, user, onLogout }: Props) {
  const [items, setItems] = useState<UserMemory[]>([]);
  const [filter, setFilter] = useState<MemoryStatus | "">("active");
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("general");
  const [kind, setKind] = useState("preference");
  const [confirmSensitive, setConfirmSensitive] = useState(false);
  const [editingId, setEditingId] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [updateJobs, setUpdateJobs] = useState<UserMemoryUpdateJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [jobError, setJobError] = useState("");
  const [retryingJobId, setRetryingJobId] = useState("");

  const recentUpdateJobs = useMemo(
    () => updateJobs.filter((job) => VISIBLE_JOB_STATUSES.has(job.status)).slice(0, RECENT_JOB_LIMIT),
    [updateJobs],
  );
  const hasActiveUpdateJobs = updateJobs.some((job) => job.status === "queued" || job.status === "processing");

  function keepForFilter(memory: UserMemory) {
    return !filter || memory.status === filter;
  }

  function upsertVisibleMemory(memory: UserMemory) {
    setItems((prev) => {
      const without = prev.filter((item) => item.id !== memory.id);
      return keepForFilter(memory) ? [memory, ...without] : without;
    });
  }

  const refreshUpdateJobs = useCallback(
    async (showLoading = true) => {
      if (showLoading) setJobsLoading(true);
      try {
        setUpdateJobs(await listUserMemoryUpdateJobs(token));
        setJobError("");
      } catch (e) {
        setJobError(e instanceof Error ? e.message : "更新任务加载失败。");
      } finally {
        if (showLoading) setJobsLoading(false);
      }
    },
    [token],
  );

  useEffect(() => {
    void refreshUpdateJobs();
  }, [refreshUpdateJobs]);

  useEffect(() => {
    if (!hasActiveUpdateJobs) return;
    const timer = window.setInterval(() => void refreshUpdateJobs(false), 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveUpdateJobs, refreshUpdateJobs]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    listUserMemories(token, filter || undefined)
      .then((data) => {
        if (active) {
          setItems(data);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : "加载失败。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [token, filter]);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!content.trim()) {
      setError("请输入记忆内容。");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const m = await createUserMemory(token, {
        content: content.trim(),
        category: category.trim() || "general",
        kind: kind.trim() || "preference",
        confirm_sensitive: confirmSensitive,
      });
      upsertVisibleMemory(m);
      setContent("");
      setCategory("general");
      setKind("preference");
      setConfirmSensitive(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败。");
    } finally {
      setSaving(false);
    }
  }

  function startEdit(m: UserMemory) {
    setEditingId(m.id);
    setDraft({
      content: m.content,
      category: m.category,
      kind: m.kind,
      status: m.status,
      revision: m.revision,
      confirmSensitive: false,
    });
  }

  async function saveEdit(memId: string) {
    if (!draft || !draft.content.trim()) {
      setError("请输入内容。");
      return;
    }

    setSaving(true);
    setError("");
    try {
      const updated = await updateUserMemory(token, memId, {
        expected_revision: draft.revision,
        content: draft.content.trim(),
        category: draft.category.trim() || "general",
        kind: draft.kind.trim() || "preference",
        status: draft.status,
        confirm_sensitive: draft.confirmSensitive,
      });
      upsertVisibleMemory(updated);
      setEditingId("");
      setDraft(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "更新失败。");
    } finally {
      setSaving(false);
    }
  }

  async function removeMem(memId: string) {
    setError("");
    try {
      await deleteUserMemory(token, memId);
      setItems((prev) => {
        if (filter) return prev.filter((m) => m.id !== memId);
        return prev.map((m) =>
          m.id === memId ? { ...m, status: "deleted", invalid_at: new Date().toISOString() } : m,
        );
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败。");
    }
  }

  async function approveMem(memId: string) {
    setError("");
    try {
      upsertVisibleMemory(await approveUserMemory(token, memId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "审批失败。");
    }
  }

  async function rejectMem(memId: string) {
    setError("");
    try {
      upsertVisibleMemory(await rejectUserMemory(token, memId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "拒绝失败。");
    }
  }

  async function restoreMem(memId: string) {
    setError("");
    try {
      upsertVisibleMemory(await restoreUserMemory(token, memId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "恢复失败。");
    }
  }

  async function purgeMem(memId: string) {
    setError("");
    try {
      await purgeUserMemory(token, memId);
      setItems((prev) => prev.filter((m) => m.id !== memId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "永久清除失败。");
    }
  }

  async function exportMemories() {
    setError("");
    try {
      const data = await exportUserMemoryData(token);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `memory-export-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败。");
    }
  }

  async function retryUpdateJob(jobId: string) {
    setRetryingJobId(jobId);
    setJobError("");
    try {
      const updated = await retryUserMemoryUpdateJob(token, jobId);
      setUpdateJobs((prev) => [updated, ...prev.filter((job) => job.id !== jobId)]);
      await refreshUpdateJobs(false);
    } catch (e) {
      setJobError(e instanceof Error ? e.message : "更新任务重试失败。");
    } finally {
      setRetryingJobId("");
    }
  }

  function confirmTitle() {
    return confirmAction?.kind === "purge" ? "永久清除记忆" : "删除记忆";
  }

  function confirmMessage() {
    return confirmAction?.kind === "purge"
      ? "此操作会删除这条记忆正文、向量索引以及记忆日志中的引用，仅保留脱敏审计记录；不会删除原始聊天消息，且不可恢复。"
      : "此操作会把记忆移到 deleted 状态，不再参与召回，可在 deleted 列表中恢复。";
  }

  return (
    <AppShell>
      <TopBar user={{ username: user.username, is_admin: user.is_admin }} onLogout={onLogout} />
      <section className={page.splitPage}>
        <aside className={page.sidePanel}>
          <h2 className={page.sectionTitle} style={{ marginBottom: 16 }}>
            添加记忆
          </h2>
          <form onSubmit={handleCreate} className={page.formStack}>
            <Textarea label="内容" value={content} onChange={(e) => setContent(e.target.value)} rows={5} />
            <Input label="分类" value={category} onChange={(e) => setCategory(e.target.value)} maxLength={80} />
            <Input label="类型" value={kind} onChange={(e) => setKind(e.target.value)} maxLength={40} />
            <label className={styles.confirmSensitive}>
              <input
                type="checkbox"
                checked={confirmSensitive}
                onChange={(event) => setConfirmSensitive(event.target.checked)}
              />
              <span>确认保存可能包含的敏感信息</span>
            </label>
            <Button type="submit" disabled={saving}>
              {saving ? "保存中..." : "保存"}
            </Button>
          </form>
          {error && (
            <p className={page.error} style={{ marginTop: 12 }}>
              {error}
            </p>
          )}
        </aside>

        <section className={page.mainPanel}>
          <section className={styles.jobsSection} aria-labelledby="memory-update-jobs-title">
            <div className={styles.jobsHeader}>
              <div>
                <h2 id="memory-update-jobs-title" className={page.sectionTitle}>
                  记忆更新任务
                </h2>
                <p className={page.subtitle}>最近的排队、处理中和失败任务</p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void refreshUpdateJobs()}
                disabled={jobsLoading}
              >
                {jobsLoading ? "刷新中..." : "刷新任务"}
              </Button>
            </div>

            {jobError && <p className={page.error}>{jobError}</p>}
            {jobsLoading && updateJobs.length === 0 && <p className={page.muted}>正在加载更新任务...</p>}
            {!jobsLoading && recentUpdateJobs.length === 0 && <p className={page.muted}>暂无待处理或失败的任务</p>}

            {recentUpdateJobs.length > 0 && (
              <div className={styles.jobList}>
                {recentUpdateJobs.map((job) => {
                  const leaseExpired = isLeaseExpired(job);
                  const queuedDispatchFailed =
                    job.status === "queued" &&
                    (!job.dispatched_at || job.error_message.startsWith("worker dispatch failed:"));
                  const retryable = queuedDispatchFailed || job.status === "failed" || leaseExpired;
                  return (
                    <div key={job.id} className={styles.jobRow}>
                      <div className={styles.jobBody}>
                        <div className={styles.jobTopLine}>
                          <StatusPill variant={job.status} label={job.status} />
                          {leaseExpired && <StatusPill variant="failed" label="租约已过期" />}
                          <span className={page.muted} style={{ fontSize: 11 }}>
                            尝试 {job.attempts} 次
                          </span>
                        </div>
                        <p className={styles.jobMessage}>{job.user_message}</p>
                        <div className={styles.jobMeta}>
                          <span>更新于 {formatTimestamp(job.updated_at)}</span>
                          {job.lease_expires_at && (
                            <span className={leaseExpired ? styles.leaseExpired : undefined}>
                              租约到期 {formatTimestamp(job.lease_expires_at)}
                            </span>
                          )}
                        </div>
                        {job.error_message && <p className={styles.jobError}>错误：{job.error_message}</p>}
                      </div>
                      {retryable && (
                        <div className={styles.jobActions}>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => void retryUpdateJob(job.id)}
                            disabled={retryingJobId === job.id}
                          >
                            {retryingJobId === job.id ? "提交中..." : job.status === "queued" ? "重新投递" : "重试"}
                          </Button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <div className={page.toolbar}>
            <div>
              <h2 className={page.sectionTitle}>记忆列表</h2>
              <p className={page.subtitle}>{filter ? `${filter} ${items.length} 条` : `未删除 ${items.length} 条`}</p>
            </div>
            <div className={page.controlRow}>
              <Button variant="secondary" size="sm" onClick={exportMemories}>
                导出
              </Button>
              <label style={{ fontSize: 13, fontWeight: 500, color: "var(--color-text-secondary)" }}>
                状态
              </label>
              <select
                value={filter}
                onChange={(e) => setFilter(e.target.value as MemoryStatus | "")}
                style={{
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "1px solid var(--color-border)",
                  fontSize: 13,
                  fontFamily: "inherit",
                  background: "var(--color-surface)",
                }}
              >
                <option value="">未删除</option>
                {STATUS_OPTS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {loading && <p className={page.muted}>正在加载...</p>}
          {!loading && items.length === 0 && <p className={page.empty}>暂无记忆</p>}

          <div className={page.cardList}>
            {items.map((m) => (
              <div key={m.id} className={page.card} style={{ padding: "14px 16px" }}>
                {editingId === m.id && draft ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    <Textarea
                      label="内容"
                      value={draft.content}
                      onChange={(e) => setDraft({ ...draft, content: e.target.value })}
                      rows={4}
                    />
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                      <Input
                        label="分类"
                        value={draft.category}
                        onChange={(e) => setDraft({ ...draft, category: e.target.value })}
                        maxLength={80}
                      />
                      <Input
                        label="类型"
                        value={draft.kind}
                        onChange={(e) => setDraft({ ...draft, kind: e.target.value })}
                      />
                      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        <label style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-secondary)" }}>
                          状态
                        </label>
                        <select
                          value={draft.status}
                          onChange={(e) => setDraft({ ...draft, status: e.target.value as MemoryStatus })}
                          style={{
                            padding: "6px 10px",
                            borderRadius: 6,
                            border: "1px solid var(--color-border)",
                            fontSize: 13,
                            fontFamily: "inherit",
                          }}
                        >
                          {STATUS_OPTS.map((s) => (
                            <option key={s} value={s}>
                              {s}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                    <label className={styles.confirmSensitive}>
                      <input
                        type="checkbox"
                        checked={draft.confirmSensitive}
                        onChange={(event) => setDraft({ ...draft, confirmSensitive: event.target.checked })}
                      />
                      <span>确认保存可能包含的敏感信息</span>
                    </label>
                    <div style={{ display: "flex", gap: 8 }}>
                      <Button size="sm" onClick={() => saveEdit(m.id)} disabled={saving}>
                        保存
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => {
                          setEditingId("");
                          setDraft(null);
                        }}
                      >
                        取消
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                      <StatusPill variant={m.status} label={m.status} />
                      <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                        {m.category} / {m.kind}
                      </span>
                    </div>
                    <p style={{ margin: "0 0 4px", fontSize: 14, lineHeight: 1.5 }}>{m.content}</p>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <small style={{ color: "var(--color-text-muted)", fontSize: 11 }}>
                        touched {m.touched_count} / merged {m.merge_count} /{" "}
                        {new Date(m.updated_at).toLocaleString()}
                      </small>
                      <div style={{ display: "flex", gap: 4 }}>
                        {m.status === "pending" && (
                          <>
                            <Button variant="secondary" size="sm" onClick={() => approveMem(m.id)}>
                              通过
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => rejectMem(m.id)}>
                              拒绝
                            </Button>
                          </>
                        )}
                        {m.status === "deleted" ? (
                          <>
                            <Button variant="secondary" size="sm" onClick={() => restoreMem(m.id)}>
                              恢复
                            </Button>
                            <Button
                              variant="danger"
                              size="sm"
                              onClick={() => setConfirmAction({ kind: "purge", memoryId: m.id })}
                            >
                              永久清除
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button variant="ghost" size="sm" onClick={() => startEdit(m)}>
                              编辑
                            </Button>
                            <Button
                              variant="danger"
                              size="sm"
                              onClick={() => setConfirmAction({ kind: "delete", memoryId: m.id })}
                            >
                              删除
                            </Button>
                          </>
                        )}
                      </div>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </section>
      </section>

      {confirmAction && (
        <ConfirmDialog
          title={confirmTitle()}
          message={confirmMessage()}
          confirmLabel={confirmAction.kind === "purge" ? "永久清除" : "删除"}
          onConfirm={() => {
            const action = confirmAction;
            setConfirmAction(null);
            if (action.kind === "purge") purgeMem(action.memoryId);
            else removeMem(action.memoryId);
          }}
          onCancel={() => setConfirmAction(null)}
        />
      )}
    </AppShell>
  );
}
