import type { RetrievalLog, SearchScope } from "../../../lib/api";
import styles from "./InsightPanel.module.css";

type Props = {
  logs: RetrievalLog[];
  selectedId: string;
  onSelect: (id: string) => void;
};

function scopeLabel(s: SearchScope | string): string {
  const map: Record<string, string> = { single: "单个知识库", department: "部门", public: "公共", accessible: "所有" };
  return map[s] ?? s;
}

function shortId(v: string): string {
  return v.length > 8 ? v.slice(0, 8) : v;
}

function valFromRecord(o: unknown, key: string): string {
  if (typeof o !== "object" || !o) return "";
  const v = (o as Record<string, unknown>)[key];
  return v != null ? String(v) : "";
}

export function RetrievalLogPanel({ logs, selectedId, onSelect }: Props) {
  const active = logs.find((l) => l.id === selectedId) ?? logs[0] ?? null;

  return (
    <div className={styles.stack}>
      {logs.length > 1 && (
        <select
          value={selectedId}
          onChange={(e) => onSelect(e.target.value)}
          className={styles.select}
        >
          {logs.map((l) => (
            <option key={l.id} value={l.id}>
              {new Date(l.created_at).toLocaleString()} · {l.query}
            </option>
          ))}
        </select>
      )}

      {!active ? (
        <p className={styles.empty}>暂无检索日志。</p>
      ) : (
        <div style={{ fontSize: 13, lineHeight: 1.6 }}>
          <div className={styles.grid}>
            <div><span className={styles.label}>scope:</span> {scopeLabel(active.scope_type)}</div>
            <div><span className={styles.label}>searched:</span> {active.searched_knowledge_base_ids.length} KBs</div>
            <div><span className={styles.label}>candidates:</span> {active.candidates.length}</div>
            <div><span className={styles.label}>selected:</span> {active.selected_chunks.length}</div>
            <div><span className={styles.label}>rrf_k:</span> {active.rrf_k}</div>
            <div><span className={styles.label}>compressed:</span> {active.compression_chars_saved} chars</div>
          </div>
          <div style={{ marginBottom: 8 }}>
            <strong>query</strong>
            <p className={styles.text}>{active.query}</p>
          </div>
          <div>
            <strong>selected_chunks</strong>
            {active.selected_chunks.slice(0, 5).map((chunk, i) => (
              <div key={i} className={`${styles.card} ${styles.softCard}`} style={{ margin: "6px 0" }}>
                <strong>#{valFromRecord(chunk, "chunk_index")} · {valFromRecord(chunk, "file_name")}</strong>
                <p className={styles.text}>{valFromRecord(chunk, "content_preview")}</p>
                <small className={styles.meta}>
                  {valFromRecord(chunk, "rrf_score") ? `rrf ${valFromRecord(chunk, "rrf_score")} · ` : ""}
                  kb {shortId(valFromRecord(chunk, "knowledge_base_id"))}
                </small>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
