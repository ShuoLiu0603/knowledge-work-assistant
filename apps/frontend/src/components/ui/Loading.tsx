import styles from "./Loading.module.css";

export function Loading({ text = "加载中..." }: { text?: string }) {
  return (
    <div className={styles.wrap}>
      <div className={styles.spinner} />
      <p>{text}</p>
    </div>
  );
}
