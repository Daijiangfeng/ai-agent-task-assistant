import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../../lib/apiClient";
import { queryKeys, taskRefetchInterval } from "../../lib/queryClient";
import {
  Card,
  EmptyState,
  PageHeader,
  ProgressBar,
  Spinner,
  StatusPill,
} from "../../components";
import { isTerminal } from "../../lib/types";
import styles from "./TaskDetailPage.module.css";

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 12)}…` : id;
}

export default function TaskDetailPage() {
  const { taskId = "" } = useParams();

  const query = useQuery({
    queryKey: queryKeys.task(taskId),
    queryFn: () => api.tasks.get(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (q) => taskRefetchInterval(q.state.data),
  });

  if (query.isLoading) {
    return (
      <div className={styles.center}>
        <Spinner label="加载任务详情…" />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <EmptyState
        title="未找到该任务"
        description="任务可能已被清理，或后端已重启（内存存储）。"
        action={<Link to="/tasks">返回任务列表</Link>}
      />
    );
  }

  const task = query.data;
  const polling = !isTerminal(task.status);

  return (
    <div>
      <PageHeader
        title="任务详情"
        subtitle={`ID ${shortId(task.task_id)}`}
        actions={<StatusPill status={task.status} />}
      />

      <Card className={styles.section}>
        <div className={styles.progressHead}>
          <h3 className={styles.sectionTitle}>执行进度</h3>
          {polling ? <span className={styles.live}>实时刷新中…</span> : null}
        </div>
        <ProgressBar value={task.progress} />
        {task.current_step ? (
          <p className={styles.step}>当前步骤：{task.current_step}</p>
        ) : null}
        <div className={styles.meta}>
          <span>计划版本 v{task.plan_version}</span>
          <span>迭代次数 {task.iteration_count}</span>
        </div>
      </Card>

      {task.error ? (
        <Card className={styles.section}>
          <h3 className={styles.sectionTitle}>错误</h3>
          <pre className={styles.error}>{task.error}</pre>
        </Card>
      ) : null}

      <Card className={styles.section}>
        <h3 className={styles.sectionTitle}>
          执行计划{task.plan?.goal ? `：${task.plan.goal}` : ""}
        </h3>
        {task.plan?.reasoning ? (
          <p className={styles.reasoning}>{task.plan.reasoning}</p>
        ) : null}
        {task.subtasks.length > 0 ? (
          <ol className={styles.subtasks}>
            {task.subtasks.map((sub, index) => (
              <li key={sub.id} className={styles.subtask}>
                <div className={styles.subtaskHead}>
                  <span className={styles.subtaskIndex}>{index + 1}</span>
                  <span className={styles.subtaskDesc}>{sub.description}</span>
                  <StatusPill status={sub.status} />
                </div>
                {sub.tool_used ? (
                  <span className={styles.tool}>工具：{sub.tool_used}</span>
                ) : null}
                {sub.result ? (
                  <pre className={styles.subResult}>{sub.result}</pre>
                ) : null}
                {sub.error ? (
                  <pre className={styles.subError}>{sub.error}</pre>
                ) : null}
              </li>
            ))}
          </ol>
        ) : (
          <EmptyState title="计划尚未生成" description="Agent 正在规划中…" />
        )}
      </Card>

      {task.reflection ? (
        <Card className={styles.section}>
          <h3 className={styles.sectionTitle}>反思评估</h3>
          <div className={styles.scores}>
            <div className={styles.score}>
              <span className={styles.scoreLabel}>是否满意</span>
              <span className={styles.scoreValue}>
                {task.reflection.is_satisfactory ? "是" : "否"}
              </span>
            </div>
            <div className={styles.score}>
              <span className={styles.scoreLabel}>准确性</span>
              <span className={styles.scoreValue}>
                {task.reflection.accuracy_score.toFixed(2)}
              </span>
            </div>
            <div className={styles.score}>
              <span className={styles.scoreLabel}>完整性</span>
              <span className={styles.scoreValue}>
                {task.reflection.completeness_score.toFixed(2)}
              </span>
            </div>
            <div className={styles.score}>
              <span className={styles.scoreLabel}>相关性</span>
              <span className={styles.scoreValue}>
                {task.reflection.relevance_score.toFixed(2)}
              </span>
            </div>
          </div>
          {task.reflection.issues.length > 0 ? (
            <ul className={styles.issues}>
              {task.reflection.issues.map((issue, i) => (
                <li key={i}>{issue}</li>
              ))}
            </ul>
          ) : null}
        </Card>
      ) : null}

      {task.final_result ? (
        <Card className={styles.section} elevated>
          <h3 className={styles.sectionTitle}>最终结果</h3>
          <pre className={styles.finalResult}>{task.final_result}</pre>
        </Card>
      ) : null}

      <div className={styles.footer}>
        <Link to="/tasks">← 返回任务列表</Link>
      </div>
    </div>
  );
}
