import { FormEvent, KeyboardEvent, useRef } from "react";
import { Button } from "../../../components/ui/Button";
import styles from "./ChatInput.module.css";

type Props = {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  streaming: boolean;
  disabled: boolean;
  memoryOff: boolean;
  onMemoryOffChange: (value: boolean) => void;
};

export function ChatInput({
  value,
  onChange,
  onSubmit,
  onStop,
  streaming,
  disabled,
  memoryOff,
  onMemoryOffChange,
}: Props) {
  const formRef = useRef<HTMLFormElement>(null);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!streaming && !disabled && value.trim()) onSubmit();
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!streaming && !disabled && value.trim()) onSubmit();
  }

  return (
    <form
      ref={formRef}
      onSubmit={handleSubmit}
      className={styles.form}
    >
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={2}
        disabled={streaming || disabled}
        placeholder="输入问题 (Enter 发送, Shift+Enter 换行)"
        className={styles.textarea}
      />
      <div className={styles.actions}>
        <label className={styles.memoryToggle}>
          <input
            type="checkbox"
            checked={memoryOff}
            onChange={(event) => onMemoryOffChange(event.target.checked)}
            disabled={streaming || disabled}
          />
          <span>本轮不使用记忆</span>
        </label>
        {streaming && (
          <Button variant="secondary" size="sm" onClick={onStop} type="button">
            停止
          </Button>
        )}
        <Button size="sm" type="submit" disabled={streaming || disabled || !value.trim()}>
          {streaming ? "生成中" : "发送"}
        </Button>
      </div>
    </form>
  );
}
