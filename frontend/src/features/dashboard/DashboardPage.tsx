import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../lib/apiClient";
import { queryKeys } from "../../lib/queryClient";
import {
  Card,
  EmptyState,
  PageHeader,
  Spinner,
  StatusPill,
} from "../../components";
import type { TaskStatus } from "../../lib/types";
import styles from "./DashboardPage.module.css";

const STATUS_ORDER: TaskStatus[] = [
  "pending",
  "planning",
  "executing",
  "reflecting",
  "replanning",
  "awaiting_approval",
  "paused",
  "cancelled",
  "completed",
  "failed",
];

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 12)}…` : id;
}

export default function DashboardPage() {
  const statsQuery = useQuery({ queryKey: queryKeys.stats, queryFn: api.stats.get });
  const toolsQuery = useQuery({ queryKey: queryKeys.tools, queryFn: api.tools.list });
  const healthQuery = useQuery({ queryKey: queryKeys.health, queryFn: api.health });
  const approvalsQuery = useQuery({
    queryKey: [...queryKeys.tasks, "approval-pending"],
    queryFn: () => api.tasks.list(50, 0, "awaiting_approval"),
  });

  if (statsQuery.isLoading) {
    return (
      <div className={styles.center}>
        <Spinner label="加载概览…" />
      </div>
    );
  }

  if (statsQuery.isError || !statsQuery.data) {
    return (
      <EmptyState
        title="无法加载系统概览"
        description="请确认后端服务已启动（默认 http://localhost:8000）。"
      />
    );
  }

  const stats = statsQuery.data;
  const pendingCount = stats.tasks_by_status?.["awaiting_approval"] ?? 0;
  const pausedCount = stats.tasks_by_status?.["paused"] ?? 0;
  const metrics = [
    { label: "任务总数", value: stats.task_total },
    { label: "可用工具", value: stats.tool_count },
  ];

  const pendingApprovals = approvalsQuery.data?.tasks ?? [];

  return (
    <div>
      <PageHeader
        title="系统概览"
        subtitle="Agent 运行态势与能力清单一览。"
      />

      <section className={styles.metrics}>
        {metrics.map((m) => (
          <Card key={m.label} className={styles.metricCard}>
            <span className={styles.metricValue}>{m.value}</span>
            <span className={styles.metricLabel}>{m.label}</span>
          </Card>
        ))}
      </section>

      <section className={styles.grid}>
        <Card className={styles.panel}>
          <h3 className={styles.panelTitle}>任务状态分布</h3>
          <div className={styles.statusList}>
            {STATUS_ORDER.map((s) => (
              <div key={s} className={styles.statusRow}>
                <StatusPill status={s} />
                <span className={styles.statusCount}>
                  {stats.tasks_by_status?.[s] ?? 0}
                </span>
              </div>
            ))}
          </div>
        </Card>

        <Card className={styles.panel}>
          <h3 className={styles.panelTitle}>系统信息</h3>
          <dl className={styles.info}>
            <div className={styles.infoRow}>
              <dt>服务版本</dt>
              <dd>{stats.version}</dd>
            </div>
            <div className={styles.infoRow}>
              <dt>健康状态</dt>
              <dd>
                {healthQuery.data
                  ? healthQuery.data.status
                  : healthQuery.isError
                    ? "不可用"
                    : "检测中…"}
              </dd>
            </div>
          </dl>
        </Card>
      </section>

      <section className={styles.approvalsSection}>
        <Card className={styles.panel}>
          <div className={styles.approvalsHead}>
            <h3 className={styles.panelTitle}>
              审批待办
              {pendingCount > 0 ? (
                <span className={styles.approvalBadge}>{pendingCount}</span>
              ) : null}
            </h3>
            {pendingApprovals.length > 0 ? (
              <Link className={styles.approvalsLink} to="/tasks">
                去任务列表处理 →
              </Link>
            ) : null}
          </div>
          {approvalsQuery.isLoading ? (
            <Spinner label="加载待办…" />
          ) : pendingApprovals.length > 0 ? (
            <ul className={styles.approvalList}>
              {pendingApprovals.map((task) => (
                <li key={task.task_id}>
                  <Link
                    className={styles.approvalItem}
                    to={`/tasks/${task.task_id}`}
                  >
                    <span className={styles.approvalGoal}>
                      {task.plan?.goal ?? `任务 ${shortId(task.task_id)}`}
                    </span>
                    <span className={styles.approvalTime}>
                      {new Date(task.created_at).toLocaleString()}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="没有待审批任务" description="一切正常。" />
          )}
        </Card>
        <Card className={styles.panel}>
          <h3 className={styles.panelTitle}>已暂停任务</h3>
          <div className={styles.statusList}>
            <div className={styles.statusRow}>
              <StatusPill status="paused" />
              <span className={styles.statusCount}>{pausedCount}</span>
            </div>
          </div>
          <p className={styles.hint}>
            暂停任务可通过任务详情页的「恢复执行」断点续跑。
          </p>
        </Card>
      </section>

      <section className={styles.toolsSection}>
        <Card className={styles.panel}>
          <h3 className={styles.panelTitle}>
            Agent 能力（{toolsQuery.data?.total ?? 0} 个工具）
          </h3>
          {toolsQuery.isLoading ? (
            <Spinner label="加载工具…" />
          ) : toolsQuery.data && toolsQuery.data.tools.length > 0 ? (
            <div className={styles.tools}>
              {toolsQuery.data.tools.map((tool) => (
                <div key={tool.name} className={styles.toolCard}>
                  <span className={styles.toolName}>{tool.name}</span>
                  <span className={styles.toolDesc}>{tool.description}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无注册工具" />
          )}
        </Card>
      </section>
    </div>
  );
}