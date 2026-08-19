import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../../lib/apiClient";
import { queryKeys, taskRefetchInterval } from "../../lib/queryClient";
import {
  Button,
  Card,
  EmptyState,
  PageHeader,
  ProgressBar,
  Spinner,
  StatusPill,
  TextArea,
  useToast,
} from "../../components";
import {
  isStuck,
  isTerminal,
  type ApprovalRequest,
  type TaskStatus,
} from "../../lib/types";
import styles from "./TaskDetailPage.module.css";

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 12)}…` : id;
}

const RUNNING: TaskStatus[] = [
  "planning",
  "executing",
  "reflecting",
  "replanning",
];

function canPause(status: TaskStatus): boolean {
  return RUNNING.includes(status) || status === "awaiting_approval";
}

function canResume(status: TaskStatus): boolean {
  return ["paused", "cancelled", "failed", "completed", "pending"].includes(
    status,
  );
}

function canRetry(status: TaskStatus): boolean {
  return ["cancelled", "failed", "completed", "paused", "pending"].includes(
    status,
  );
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

export default function TaskDetailPage() {
  const { taskId = "" } = useParams();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");

  const query = useQuery({
    queryKey: queryKeys.task(taskId),
    queryFn: () => api.tasks.get(taskId),
    enabled: Boolean(taskId),
    refetchInterval: (q) => taskRefetchInterval(q.state.data),
  });

  function refresh() {
    queryClient.invalidateQueries({ queryKey: queryKeys.task(taskId) });
    queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
  }

  const controlMutation = useMutation({
    mutationFn: async (action: "pause" | "resume" | "cancel" | "retry") => {
      switch (action) {
        case "pause":
          return api.tasks.pause(taskId);
        case "resume":
          return api.tasks.resume(taskId);
        case "cancel":
          return api.tasks.cancel(taskId);
        default:
          return api.tasks.retry(taskId);
      }
    },
    onSuccess: () => {
      toast.notify("操作已提交", "success");
      refresh();
    },
    onError: (err) => {
      toast.notify(
        err instanceof ApiError ? err.detail : "操作失败，请重试",
        "error",
      );
    },
  });

  const retryFromMutation = useMutation({
    mutationFn: (fromIndex: number) => api.tasks.retry(taskId, fromIndex),
    onSuccess: () => {
      toast.notify("已从此步骤重新执行", "success");
      refresh();
    },
    onError: (err) => {
      toast.notify(
        err instanceof ApiError ? err.detail : "重试失败，请重试",
        "error",
      );
    },
  });

  const approvalMutation = useMutation({
    mutationFn: async (vars: {
      approve: boolean;
      note?: string;
      modifiedArgs?: Record<string, unknown>;
    }) => {
      const pending = query.data?.pending_approval;
      if (!pending) throw new ApiError(404, "审批请求不存在");
      return vars.approve
        ? api.tasks.approve(taskId, pending.id, vars.note, vars.modifiedArgs)
        : api.tasks.reject(taskId, pending.id, vars.note);
    },
    onSuccess: (_, vars) => {
      toast.notify(vars.approve ? "已批准并继续执行" : "已拒绝", "success");
      setNote("");
      refresh();
    },
    onError: (err) => {
      toast.notify(
        err instanceof ApiError ? err.detail : "审批操作失败",
        "error",
      );
    },
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
  const polling = !isTerminal(task.status) && !isStuck(task.status);
  const pending = task.pending_approval;

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
          <span className={styles.meta}>
            {task.execution_mode === "multi_agent"
              ? "多 Agent 协作"
              : task.execution_mode
                ? "单 Agent"
                : ""}
            {polling ? <span className={styles.live}>实时刷新中…</span> : null}
          </span>
        </div>
        <ProgressBar value={task.progress} />
        {task.current_step ? (
          <p className={styles.step}>当前步骤：{task.current_step}</p>
        ) : null}
        <div className={styles.meta}>
          <span>计划版本 v{task.plan_version}</span>
          <span>迭代次数 {task.iteration_count}</span>
        </div>

        <div className={styles.actions}>
          {canPause(task.status) ? (
            <Button
              variant="ghost"
              loading={controlMutation.isPending}
              onClick={() => controlMutation.mutate("pause")}
            >
              暂停
            </Button>
          ) : null}
          {canResume(task.status) ? (
            <Button
              variant="ghost"
              loading={controlMutation.isPending}
              onClick={() => controlMutation.mutate("resume")}
            >
              恢复执行
            </Button>
          ) : null}
          {canRetry(task.status) ? (
            <Button
              variant="ghost"
              loading={controlMutation.isPending}
              onClick={() => controlMutation.mutate("retry")}
            >
              重新执行
            </Button>
          ) : null}
          {!isTerminal(task.status) ? (
            <Button
              variant="ghost"
              loading={controlMutation.isPending}
              onClick={() => controlMutation.mutate("cancel")}
            >
              取消
            </Button>
          ) : null}
          {task.status === "awaiting_approval" && pending ? (
            <span className={styles.approvalHint}>
              等待人工审批：{pending.tool_name}
            </span>
          ) : null}
        </div>
      </Card>

      {task.error ? (
        <Card className={styles.section}>
          <h3 className={styles.sectionTitle}>错误</h3>
          <pre className={styles.error}>{task.error}</pre>
        </Card>
      ) : null}

      {pending ? (
        <ApprovalCard
          approval={pending}
          note={note}
          onNoteChange={setNote}
          onApprove={(modifiedArgs) =>
            approvalMutation.mutate({
              approve: true,
              note: note.trim() || undefined,
              modifiedArgs,
            })
          }
          onReject={() =>
            approvalMutation.mutate({
              approve: false,
              note: note.trim() || undefined,
            })
          }
          busy={approvalMutation.isPending}
        />
      ) : null}

      {task.approval_history.length > 0 ? (
        <Card className={styles.section}>
          <h3 className={styles.sectionTitle}>审批历史</h3>
          <ul className={styles.approvalList}>
            {task.approval_history.map((a) => (
              <li key={a.id} className={styles.approvalItem}>
                <div className={styles.approvalHead}>
                  <span className={styles.approvalTool}>{a.tool_name}</span>
                  <StatusPill
                    status={
                      a.status === "approved"
                        ? "completed"
                        : a.status === "rejected"
                          ? "failed"
                          : "pending"
                    }
                  />
                </div>
                <span className={styles.approvalMeta}>
                  {formatTime(a.created_at)}
                  {a.decision_note ? ` · ${a.decision_note}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {task.agent_results.length > 0 ? (
        <Card className={styles.section}>
          <h3 className={styles.sectionTitle}>多 Agent 执行结果</h3>
          <div className={styles.agents}>
            {task.agent_results.map((agent) => (
              <div key={`${agent.role}-${agent.status}`} className={styles.agent}>
                <div className={styles.agentHead}>
                  <span className={styles.agentRole}>{agent.role}</span>
                  <StatusPill
                    status={
                      agent.status === "completed" ? "completed" : "failed"
                    }
                  />
                  {agent.latency_ms != null ? (
                    <span className={styles.approvalMeta}>
                      {(agent.latency_ms / 1000).toFixed(1)}s
                    </span>
                  ) : null}
                </div>
                {agent.objective ? (
                  <p className={styles.agentObjective}>{agent.objective}</p>
                ) : null}
                {agent.result ? (
                  <pre className={styles.subResult}>{agent.result}</pre>
                ) : null}
                {agent.error ? (
                  <pre className={styles.subError}>{agent.error}</pre>
                ) : null}
              </div>
            ))}
          </div>
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
                  {sub.status === "failed" ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={retryFromMutation.isPending}
                      onClick={() => retryFromMutation.mutate(index)}
                    >
                      从此步重试
                    </Button>
                  ) : null}
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

interface ApprovalCardProps {
  approval: ApprovalRequest;
  note: string;
  onNoteChange: (value: string) => void;
  onApprove: (modifiedArgs?: Record<string, unknown>) => void;
  onReject: () => void;
  busy: boolean;
}

function ApprovalCard({
  approval,
  note,
  onNoteChange,
  onApprove,
  onReject,
  busy,
}: ApprovalCardProps) {
  const [argsText, setArgsText] = useState(
    JSON.stringify(approval.args, null, 2),
  );
  const [argsError, setArgsError] = useState("");

  function handleApprove() {
    let modifiedArgs: Record<string, unknown> | undefined;
    if (argsText.trim()) {
      try {
        modifiedArgs = JSON.parse(argsText);
      } catch {
        setArgsError("参数 JSON 格式不正确");
        return;
      }
    }
    setArgsError("");
    onApprove(modifiedArgs);
  }

  return (
    <Card className={styles.section} elevated>
      <h3 className={styles.sectionTitle}>
        待审批操作：{approval.tool_name}
      </h3>
      {approval.reason ? (
        <p className={styles.approvalReason}>{approval.reason}</p>
      ) : null}
      <label className={styles.fieldLabel} htmlFor="approval-args">
        工具参数（可修改后批准）
      </label>
      <textarea
        id="approval-args"
        className={styles.argsEditor}
        rows={6}
        spellCheck={false}
        value={argsText}
        onChange={(e) => setArgsText(e.target.value)}
      />
      {argsError ? (
        <p className={styles.argsError}>{argsError}</p>
      ) : null}
      <TextArea
        label="决策备注（可选）"
        name="approval-note"
        value={note}
        onChange={(e) => onNoteChange(e.target.value)}
        rows={2}
        placeholder="例如：参数已修改 / 拒绝原因"
      />
      <div className={styles.approvalActions}>
        <Button loading={busy} onClick={handleApprove}>
          批准并继续
        </Button>
        <Button variant="ghost" loading={busy} onClick={onReject}>
          拒绝
        </Button>
      </div>
    </Card>
  );
}