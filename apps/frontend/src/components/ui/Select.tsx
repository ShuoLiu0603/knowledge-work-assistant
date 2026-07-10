import { type SelectHTMLAttributes } from "react";
import styles from "./Input.module.css";

type Props = SelectHTMLAttributes<HTMLSelectElement> & {
  label?: string;
  options: { value: string; label: string; disabled?: boolean }[];
};

export function Select({ label, options, className = "", id, ...rest }: Props) {
  const inputId = id ?? label?.replace(/\s+/g, "-").toLowerCase();
  return (
    <div className={styles.field}>
      {label && (
        <label className={styles.label} htmlFor={inputId}>
          {label}
        </label>
      )}
      <select id={inputId} className={`${styles.input} ${className}`} {...rest}>
        {options.map((opt) => (
          <option key={opt.value} value={opt.value} disabled={opt.disabled}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
}
