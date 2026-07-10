import type { AgentRun } from "../../../lib/api";
import styles from "./InsightPanel.module.css";

type Props = {
  runs: AgentRun[];
  selectedId: string;
  onSelect: (id: string) => void;
};

export function AgentTracePanel({ runs, selectedId, onSelect }: Props) {
  const active = runs.find((r) => r.id === selectedId) ?? runs[0] ?? null;

  return (
    <div className={styles.stack}>
      {runs.length > 1 && (
        <select
          value={selectedId}
          onChange={(e) => onSelect(e.target.value)}
          className={styles.select}
        >
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              {new Date(r.created_at).toLocaleString()} · {r.intent}
            </option>
          ))}
        </select>
      )}

      {!active ? (
        <p className={styles.empty}>暂无 Agent trace。</p>
      ) : (
        <div style={{ fontSize: 13 }}>
          <div className={styles.grid}>
            <div><span className={styles.label}>intent:</span> {active.intent}</div>
            <div><span className={styles.label}>status:</span> {active.status}</div>
          </div>
          {active.trace.map((step, i) => (
            <div key={`${step.node}-${i}`} className={styles.card}>
              <strong>{i + 1}. {step.node}</strong>
              <p className={styles.text}>{step.action}</p>
              <small className={styles.codeMeta}>
                {JSON.stringify(step.output)}
              </small>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
