import type { Citation } from "../../../lib/api";
import styles from "./InsightPanel.module.css";

type Props = {
  citations: Citation[];
  showsSecurityLevel: boolean;
};

export function CitationPanel({ citations, showsSecurityLevel }: Props) {
  if (citations.length === 0) {
    return <p className={styles.empty}>暂无引用。</p>;
  }

  return (
    <div className={styles.stack}>
      {citations.map((c, i) => (
        <div key={`${c.chunk_id}-${i}`} className={`${styles.card} ${styles.softCard}`}>
          <div className={styles.title}>
            [{i + 1}] {c.file_name}
          </div>
          <p className={styles.text}>{c.content_preview}</p>
          <small className={styles.meta}>
            chunk #{c.chunk_index} · score: {c.score.toFixed(2)}
            {c.rrf_score != null ? ` · rrf ${c.rrf_score.toFixed(2)}` : ""}
            {showsSecurityLevel ? ` · L${c.security_level}` : ""}
            {c.page_number ? ` · page ${c.page_number}` : ""}
            {c.title_path ? ` · ${c.title_path}` : ""}
          </small>
        </div>
      ))}
    </div>
  );
}
