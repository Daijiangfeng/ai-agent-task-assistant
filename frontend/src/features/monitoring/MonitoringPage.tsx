import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../lib/apiClient";
import { queryKeys, taskRefetchInterval } from "../../lib/queryClient";
import {
  Card,
  EmptyState,
  PageHeader,
  Spinner,
  StatusPill,
} from "../../components";
import { isTerminal, type TaskStatusResponse } from "../../lib/types";
import styles from "./MonitoringPage.module.css";

type StageState = "done" | "active" | "pending";

interface Stage {
  key: string;
  title: string;
  state: StageState;
  detail: string;
}

/** 依据增强后的任务状态推导 Planner→Executor→Reflection→Replan 阶段。 */
function deriveStages(task: TaskStatusResponse): Stage[] {
  const hasPlan = Boolean(task.plan) || task.subtasks.length > 0;
  const executed = task.subtasks.filter((s) => isTerminal(s.status)).length;
  const total = task.subtasks.length;
  const hasReflection = Boolean(task.reflection);
  const replanned = task.plan_version > 1 || task.iteration_count > 1;

  return [
    {
      key: "planner",
      title: "Planner · 规划",
      state: hasPlan ? "done" : task.status === "planning" ? "active" : "pending",
      detail: hasPlan
        ? `生成 ${total} 个子任务（计划 v${task.plan_version}）`
        : "等待规划",
    },
    {
      key: "executor",
      title: "Executor · 执行",
      state:
        total > 0 && executed === total
          ? "done"
          : task.status === "executing"
            ? "active"
            : total > 0
              ? "active"
              : "pending",
      detail: total > 0 ? `已执行 ${executed}/${total} 个子任务` : "等待执行",
    },
    {
      key: "reflection",
      title: "Reflection · 反思",
      state: hasReflection
        ? "done"
        : task.status === "reflecting"
          ? "active"
          : "pending",
      detail: hasReflection
        ? `满意度：${task.reflection?.is_satisfactory ? "满意" : "不满意"}`
        : "等待反思评估",
    },
    {
      key: "replan",
      title: "Replanner · 重规划",
      state: replanned
        ? "done"
        : task.status === "replanning"
          ? "active"
          : "pending",
      detail: replanned
        ? `已重规划（迭代 ${task.iteration_count} 次）`
        : "无需重规划",
    },
  ];
}

function MonitoringDetail({ taskId }: { taskId: string }) {
  const query = useQuery({
    queryKey: queryKeys.task(taskId),
    queryFn: () => api.tasks.get(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (q) => taskRefetchInterval(q.state.data),
  });

  if (query.isLoading) {
    return (
      <div className={styles.center}>
        <Spinner label="加载执行状态…" />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <EmptyState
        title="未找到该任务"
        description="任务可能已被清理，或后端已重启。"
        action={<Link to="/monitoring">返回监控列表</Link>}
      />
    );
  }

  const task = query.data;
  const stages = deriveStages(task);
  const polling = !isTerminal(task.status);

  return (
    <div>
      <PageHeader
        title="执行监控"
        subtitle={`ID ${task.task_id.slice(0, 12)}…`}
        actions={<StatusPill status={task.status} />}
      />

      <div className={styles.metrics}>
        <Card className={styles.metric}>
          <span className={styles.metricValue}>{task.progress}%</span>
          <span className={styles.metricLabel}>整体进度</span>
        </Card>
        <Card className={styles.metric}>
          <span className={styles.metricValue}>v{task.plan_version}</span>
          <span className={styles.metricLabel}>计划版本</span>
        </Card>
        <Card className={styles.metric}>
          <span className={styles.metricValue}>{task.iteration_count}</span>
          <span className={styles.metricLabel}>迭代次数</span>
        </Card>
        <Card className={styles.metric}>
          <span className={styles.metricValue}>{task.subtasks.length}</span>
          <span className={styles.metricLabel}>子任务数</span>
        </Card>
      </div>

      <Card className={styles.timelineCard}>
        <div className={styles.timelineHead}>
          <h3 className={styles.sectionTitle}>执行阶段</h3>
          {polling ? <span className={styles.live}>实时刷新中…</span> : null}
        </div>
        <ol className={styles.timeline}>
          {stages.map((stage) => (
            <li key={stage.key} className={styles.stage} data-state={stage.state}>
              <span className={styles.dot} aria-hidden />
              <div className={styles.stageBody}>
                <span className={styles.stageTitle}>{stage.title}</span>
                <span className={styles.stageDetail}>{stage.detail}</span>
              </div>
            </li>
          ))}
        </ol>
      </Card>

      {task.subtasks.length > 0 ? (
        <Card className={styles.timelineCard}>
          <h3 className={styles.sectionTitle}>子任务与工具</h3>
          <ul className={styles.subtasks}>
            {task.subtasks.map((sub, i) => (
              <li key={sub.id} className={styles.subtask}>
                <span className={styles.subtaskIndex}>{i + 1}</span>
                <span className={styles.subtaskDesc}>{sub.description}</span>
                <span className={styles.subtaskTool}>{sub.tool_used ?? "—"}</span>
                <StatusPill status={sub.status} />
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {task.error ? (
        <Card className={styles.timelineCard}>
          <h3 className={styles.sectionTitle}>错误</h3>
          <pre className={styles.error}>{task.error}</pre>
        </Card>
      ) : null}

      <div className={styles.footer}>
        <Link to="/monitoring">← 选择其他任务</Link>
      </div>
    </div>
  );
}

function MonitoringList() {
  const navigate = useNavigate();
  const listQuery = useQuery({
    queryKey: queryKeys.taskList(50, 0),
    queryFn: () => api.tasks.list(50, 0),
    refetchInterval: 3_000,
  });

  return (
    <div>
      <PageHeader
        title="执行监控"
        subtitle="选择一个任务，查看其 Planner→Executor→Reflection→Replan 全过程。"
      />
      <Card className={styles.timelineCard}>
        {listQuery.isLoading ? (
          <Spinner label="加载任务…" />
        ) : listQuery.data && listQuery.data.tasks.length > 0 ? (
          <ul className={styles.pickList}>
            {listQuery.data.tasks.map((t) => (
              <li key={t.task_id}>
                <button
                  className={styles.pickItem}
                  onClick={() => navigate(`/monitoring/${t.task_id}`)}
                >
                  <span className={styles.pickId}>{t.task_id.slice(0, 12)}…</span>
                  <span className={styles.pickGoal}>{t.plan?.goal ?? "—"}</span>
                  <StatusPill status={t.status} />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title="暂无任务可监控"
            description="先到任务控制台创建并执行任务。"
            action={<Link to="/tasks">前往任务控制台</Link>}
          />
        )}
      </Card>
    </div>
  );
}

export default function MonitoringPage() {
  const { taskId } = useParams();
  return taskId ? <MonitoringDetail taskId={taskId} /> : <MonitoringList />;
}
