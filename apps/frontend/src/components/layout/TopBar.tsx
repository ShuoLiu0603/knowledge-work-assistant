import { Link, useLocation } from "react-router-dom";
import { Button } from "../ui/Button";
import styles from "./TopBar.module.css";

type Props = {
  user: { username: string; is_admin: boolean };
  onLogout: () => void;
};

export function TopBar({ user, onLogout }: Props) {
  const { pathname } = useLocation();
  const linkClass = (path: string) => (pathname.startsWith(path) ? styles.active : "");

  return (
    <header className={styles.bar}>
      <div className={styles.left}>
        <span className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">AR</span>
          Agentic RAG
        </span>
        <nav className={styles.nav}>
          <Link to="/chat" className={linkClass("/chat")}>问答</Link>
          <Link to="/knowledge-bases" className={linkClass("/knowledge-bases")}>知识库</Link>
          <Link to="/memories" className={linkClass("/memories")}>记忆</Link>
          {user.is_admin && <Link to="/admin" className={linkClass("/admin")}>管理</Link>}
        </nav>
      </div>
      <div className={styles.right}>
        <span className={styles.username} title={user.username}>{user.username}</span>
        <Button variant="ghost" size="sm" onClick={onLogout}>退出</Button>
      </div>
    </header>
  );
}
