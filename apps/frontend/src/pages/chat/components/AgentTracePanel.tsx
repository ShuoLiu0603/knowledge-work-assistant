import type { AgentRun, AgentTraceStep } from "../../../lib/api";
import styles from "./InsightPanel.module.css";

type Props = {
  runs: AgentRun[];
  selectedId: string;
  onSelect: (id: string) => void;
};

const ACTION_LABELS: Record<string, string> = {
  load_core_context: "加载核心上下文",
  load_context_skipped: "跳过记忆上下文",
  call_tools: "模型调用工具",
  respond: "模型生成回答",
  memory: "记忆检索",
  rag: "知识库检索",
  final_answer: "最终回答",
  cancel: "运行已取消",
  timeout: "运行超时",
  error: "运行失败",
  defer_user_memories: "延迟记忆写入",
  update_user_memories: "更新长期记忆",
  update_user_memories_skipped: "跳过记忆写入",
  update_user_memories_disabled: "记忆写入已禁用",
  complete: "运行完成",
};

function stepLabel(step: AgentTraceStep): string {
  return ACTION_LABELS[step.action] ?? step.action;
}

function stepSummary(step: AgentTraceStep): string {
  const output = step.output;
  if (step.action === "call_tools") {
    const tools = Array.isArray(output.tools) ? output.tools : [];
    const calls = tools.map((item) => {
      if (typeof item !== "object" || !item) return "";
      const call = item as Record<string, unknown>;
      return `${String(call.name ?? "tool")}(${String(call.query ?? "")})`;
    }).filter(Boolean);
    return [`模型步骤 ${output.model_step ?? "-"}`, ...calls].join(" · ");
  }
  if (step.action === "respond") {
    return `模型步骤 ${output.model_step ?? "-"}`;
  }
  if (step.action === "memory" || step.action === "rag") {
    return [
      String(output.status ?? "-"),
      `${output.result_count ?? 0} 条结果`,
      `${output.new_result_count ?? 0} 条新增`,
    ].join(" · ");
  }
  if (step.action === "complete") {
    return String(output.status ?? "-");
  }
  const answerChars = output.answer_chars;
  if (answerChars !== undefined) return `${answerChars} 字符`;
  const error = output.error_message;
  if (typeof error === "string" && error) return error;
  return "";
}

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
              {new Date(r.created_at).toLocaleString()} · {r.status}
            </option>
          ))}
        </select>
      )}

      {!active ? (
        <p className={styles.empty}>暂无 Agent trace。</p>
      ) : (
        <div style={{ fontSize: 13 }}>
          <div className={styles.grid}>
            <div><span className={styles.label}>status:</span> {active.status}</div>
            <div><span className={styles.label}>steps:</span> {active.trace.length}</div>
          </div>
          {active.trace.map((step, i) => (
            <div key={`${step.node}-${i}`} className={styles.card}>
              <strong>{i + 1}. {stepLabel(step)}</strong>
              {stepSummary(step) && <p className={styles.text}>{stepSummary(step)}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
