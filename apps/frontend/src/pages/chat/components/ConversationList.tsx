import type { Conversation } from "../../../lib/api";
import { Button } from "../../../components/ui/Button";
import styles from "./ConversationList.module.css";

type Props = {
  conversations: Conversation[];
  activeId: string;
  onSelect: (c: Conversation) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
  disabled: boolean;
};

export function ConversationList({ conversations, activeId, onSelect, onDelete, onNew, disabled }: Props) {
  return (
    <div className={styles.wrap}>
      <Button className={styles.newButton} variant="secondary" size="sm" onClick={onNew} disabled={disabled}>
        新会话
      </Button>
      <div className={styles.list}>
        {conversations.length === 0 && <p className={styles.empty}>暂无会话，创建后会显示在这里。</p>}
        {conversations.map((conv) => {
          const isActive = conv.id === activeId;
          return (
            <div
              key={conv.id}
              className={`${styles.item} ${isActive ? styles.itemActive : ""}`}
            >
              <button
                type="button"
                onClick={() => onSelect(conv)}
                disabled={disabled}
                className={styles.selectButton}
              >
                <div className={styles.title}>{conv.title}</div>
                <div className={styles.meta}>
                  {conv.target_label} · {new Date(conv.updated_at).toLocaleDateString()}
                </div>
              </button>
              <button
                type="button"
                onClick={() => onDelete(conv.id)}
                disabled={disabled}
                className={styles.deleteButton}
                title="删除"
                aria-label="删除会话"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
