import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Citation, Message, RetrievalLog, AgentRun } from "../../../lib/api";
import { Button } from "../../../components/ui/Button";
import styles from "./MessageBubble.module.css";

type Props = {
  message: Message;
  streaming: boolean;
  retrievalLogs: RetrievalLog[];
  agentRuns: AgentRun[];
  onViewCitations: (messageId: string) => void;
  onViewRetrieval: (logId: string) => void;
  onViewAgentTrace: (runId: string) => void;
};

export function MessageBubble({
  message,
  streaming,
  retrievalLogs,
  agentRuns,
  onViewCitations,
  onViewRetrieval,
  onViewAgentTrace,
}: Props) {
  const isUser = message.role === "user";
  const hasRetrieval = retrievalLogs.some((l) => l.message_id === message.id);
  const hasAgent = agentRuns.some((r) => r.message_id === message.id);
  const isThinking = streaming && !isUser && message.content === "" && !message.error_message;

  async function handleCopy() {
    await navigator.clipboard?.writeText(message.content);
  }

  return (
    <article className={`${styles.bubble} ${isUser ? styles.user : styles.assistant}`}>
      <div className={styles.meta}>
        <strong>{isUser ? "你" : "助手"}</strong>
        <span>{message.status}</span>
      </div>

      {message.error_message ? (
        <p className={styles.error}>{message.error_message}</p>
      ) : isThinking ? (
        <div className={styles.thinking}>
          <span className={styles.dot} />
          <span className={styles.dot} />
          <span className={styles.dot} />
        </div>
      ) : isUser ? (
        <p className={styles.text}>{message.content}</p>
      ) : (
        <div className={styles.markdown}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {message.content}
          </ReactMarkdown>
        </div>
      )}

      {!isUser && message.citations.length > 0 && (
        <div className={styles.actions}>
          <Button variant="ghost" size="sm" onClick={() => onViewCitations(message.id)}>
            引用 {message.citations.length}
          </Button>
          {hasRetrieval && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onViewRetrieval(retrievalLogs.find((l) => l.message_id === message.id)?.id ?? "")}
            >
              检索解释
            </Button>
          )}
          {hasAgent && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onViewAgentTrace(agentRuns.find((r) => r.message_id === message.id)?.id ?? "")}
            >
              Agent trace
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={handleCopy}>
            复制
          </Button>
        </div>
      )}
    </article>
  );
}
