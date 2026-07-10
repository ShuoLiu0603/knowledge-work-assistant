import styles from "./AppShell.module.css";

type Props = { children: React.ReactNode };

export function AppShell({ children }: Props) {
  return <div className={styles.shell}>{children}</div>;
}
