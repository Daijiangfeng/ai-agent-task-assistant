import { NavLink, Outlet } from "react-router-dom";
import styles from "./NavShell.module.css";

interface NavItem {
  to: string;
  label: string;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "系统概览", end: true },
  { to: "/tasks", label: "任务控制台" },
  { to: "/templates", label: "任务模板" },
  { to: "/monitoring", label: "执行监控" },
];

/** 侧栏 + 内容区外壳，配合 frosted 顶栏。 */
export function NavShell() {
  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <span className={styles.mark}>◈</span>
          <span className={styles.brandText}>Agent 控制台</span>
        </div>
        <nav className={styles.nav}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                isActive ? `${styles.link} ${styles.active}` : styles.link
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className={styles.footer}>企业级 AI Agent 任务执行助手</div>
      </aside>
      <main className={styles.main}>
        <div className={styles.content}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
