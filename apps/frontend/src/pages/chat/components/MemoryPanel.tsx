import { FormEvent } from "react";
import type { UserMemory } from "../../../lib/api";
import styles from "./InsightPanel.module.css";

type Props = {
  memories: UserMemory[];
  newMemory: string;
  onNewMemoryChange: (v: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
};

export function MemoryPanel({ memories, newMemory, onNewMemoryChange, onCreate, onDelete }: Props) {
  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (newMemory.trim()) onCreate();
  }

  return (
    <div className={styles.stack}>
      <form onSubmit={handleSubmit} className={styles.inlineForm}>
        <textarea
          value={newMemory}
          onChange={(e) => onNewMemoryChange(e.target.value)}
          rows={2}
          placeholder="手动添加偏好或背景"
          className={styles.textarea}
        />
        <button
          type="submit"
          disabled={!newMemory.trim()}
          className={styles.addButton}
        >
          添加
        </button>
      </form>

      {memories.length === 0 ? (
        <p className={styles.empty}>暂无长期记忆。</p>
      ) : (
        memories.slice(0, 8).map((mem) => (
          <div key={mem.id} className={styles.card}>
            <div className={styles.title}>{mem.category}</div>
            <p className={styles.text}>{mem.content}</p>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <small className={styles.meta}>
                {mem.status} · touched {mem.touched_count}
              </small>
              <button
                type="button"
                onClick={() => onDelete(mem.id)}
                className={styles.dangerLink}
              >
                删除
              </button>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
