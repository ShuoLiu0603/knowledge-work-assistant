import { useEffect, useRef } from "react";
import type { Citation, Message, RetrievalLog, AgentRun } from "../../../lib/api";
import { MessageBubble } from "./MessageBubble";
import styles from "./MessageList.module.css";

type Props = {
  messages: Message[];
  streaming: boolean;
  retrievalLogs: RetrievalLog[];
  agentRuns: AgentRun[];
  onViewCitations: (messageId: string) => void;
  onViewRetrieval: (logId: string) => void;
  onViewAgentTrace: (runId: string) => void;
};

export function MessageList({
  messages,
  streaming,
  retrievalLogs,
  agentRuns,
  onViewCitations,
  onViewRetrieval,
  onViewAgentTrace,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);

  useEffect(() => {
    if (messages.length === 0) {
      prevCountRef.current = 0;
      return;
    }
    if (messages.length !== prevCountRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
    prevCountRef.current = messages.length;
  }, [messages.length]);

  return (
    <div className={styles.list}>
      {messages.length === 0 && (
        <div className={styles.empty}>
          <div>
            <strong>开始一次可追溯问答</strong>
            <span>选择知识库后输入问题，引用、检索日志和 Agent trace 会在右侧同步展示。</span>
          </div>
        </div>
      )}
      {messages.map((m) => (
        <MessageBubble
          key={m.id}
          message={m}
          streaming={streaming}
          retrievalLogs={retrievalLogs}
          agentRuns={agentRuns}
          onViewCitations={onViewCitations}
          onViewRetrieval={onViewRetrieval}
          onViewAgentTrace={onViewAgentTrace}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
