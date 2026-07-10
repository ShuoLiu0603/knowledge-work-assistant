import type { LlmCallLog } from "../../../lib/api";
import styles from "./InsightPanel.module.css";

type Props = { logs: LlmCallLog[] };

export function LlmCallLogPanel({ logs }: Props) {
  if (logs.length === 0) {
    return <p className={styles.empty}>暂无 LLM 调用记录。</p>;
  }

  return (
    <div className={styles.stack}>
      {logs.slice(0, 6).map((log) => (
        <div key={log.id} className={styles.card}>
          <strong>
            {log.agent_name ?? "unknown"} · {log.status}
          </strong>
          <p className={styles.text}>
            {log.provider}/{log.model_name}
          </p>
          <small className={styles.meta}>
            tokens {log.total_tokens} · latency {log.latency_ms ?? "-"} ms
            {log.fallback_used ? " · fallback" : ""}
            {log.error_message ? ` · ${log.error_message}` : ""}
          </small>
        </div>
      ))}
    </div>
  );
}
