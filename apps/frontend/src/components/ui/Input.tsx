import { type InputHTMLAttributes } from "react";
import styles from "./Input.module.css";

type Props = InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
  error?: string;
};

export function Input({ label, error, className = "", id, ...rest }: Props) {
  const inputId = id ?? label?.replace(/\s+/g, "-").toLowerCase();
  const errorId = error && inputId ? `${inputId}-error` : undefined;
  return (
    <div className={styles.field}>
      {label && (
        <label className={styles.label} htmlFor={inputId}>
          {label}
        </label>
      )}
      <input
        id={inputId}
        className={`${styles.input} ${error ? styles.invalid : ""} ${className}`}
        aria-invalid={Boolean(error)}
        aria-describedby={errorId}
        {...rest}
      />
      {error && <p id={errorId} className={styles.error}>{error}</p>}
    </div>
  );
}
