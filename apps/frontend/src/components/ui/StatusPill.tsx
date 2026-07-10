import styles from "./StatusPill.module.css";

type Props = {
  variant: "active" | "pending" | "streaming" | "completed" | "failed" | "indexed" | string;
  label: string;
};

export function StatusPill({ variant, label }: Props) {
  return <span className={`${styles.pill} ${styles[variant] ?? ""}`}>{label}</span>;
}
