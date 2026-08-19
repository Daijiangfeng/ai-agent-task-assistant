import type { TaskStatus } from "../lib/types";
import styles from "./StatusPill.module.css";

const LABELS: Record<TaskStatus, string> = {
  pending: "待执行",
  planning: "规划中",
  executing: "执行中",
  reflecting: "反思中",
  replanning: "重规划",
  awaiting_approval: "待审批",
  paused: "已暂停",
  cancelled: "已取消",
  completed: "已完成",
  failed: "失败",
};

interface StatusPillProps {
  status: TaskStatus;
}

/** 10 态映射色的状态标签。 */
export function StatusPill({ status }: StatusPillProps) {
  return (
    <span className={styles.pill} data-status={status}>
      <span className={styles.dot} aria-hidden />
      {LABELS[status]}
    </span>
  );
}
