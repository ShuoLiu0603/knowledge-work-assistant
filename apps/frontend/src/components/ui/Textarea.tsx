import { type TextareaHTMLAttributes } from "react";
import styles from "./Input.module.css";

type Props = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label?: string;
  error?: string;
};

export function Textarea({ label, error, className = "", id, ...rest }: Props) {
  const inputId = id ?? label?.replace(/\s+/g, "-").toLowerCase();
  const errorId = error && inputId ? `${inputId}-error` : undefined;
  return (
    <div className={styles.field}>
      {label && (
        <label className={styles.label} htmlFor={inputId}>
          {label}
        </label>
      )}
      <textarea
        id={inputId}
        className={`${styles.input} ${styles.textarea} ${error ? styles.invalid : ""} ${className}`}
        aria-invalid={Boolean(error)}
        aria-describedby={errorId}
        {...rest}
      />
      {error && <p id={errorId} className={styles.error}>{error}</p>}
    </div>
  );
}
