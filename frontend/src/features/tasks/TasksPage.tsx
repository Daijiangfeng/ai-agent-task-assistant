import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../../lib/apiClient";
import { queryKeys } from "../../lib/queryClient";
import {
  Card,
  EmptyState,
  PageHeader,
  Spinner,
  StatusPill,
  Table,
  useToast,
} from "../../components";
import type { TaskResponse } from "../../lib/types";
import { CreateTaskForm } from "./CreateTaskForm";
import styles from "./TasksPage.module.css";

function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

export default function TasksPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({
    queryKey: queryKeys.taskList(50, 0),
    queryFn: () => api.tasks.list(50, 0),
  });

  const createMutation = useMutation({
    mutationFn: async (vars: { goal: string; context: string }) => {
      const created = await api.tasks.create(vars.goal, vars.context || undefined);
      await api.tasks.execute(created.task_id);
      return created;
    },
    onSuccess: (created) => {
      toast.notify("任务已创建并开始执行", "success");
      queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
      navigate(`/tasks/${created.task_id}`);
    },
    onError: (err) => {
      toast.notify(
        err instanceof ApiError ? err.detail : "创建任务失败，请重试",
        "error",
      );
    },
  });

  const columns = [
    {
      key: "task_id",
      header: "任务 ID",
      width: "140px",
      render: (row: TaskResponse) => (
        <span className={styles.mono}>{shortId(row.task_id)}</span>
      ),
    },
    {
      key: "goal",
      header: "目标",
      render: (row: TaskResponse) => (
        <span className={styles.goal}>{row.plan?.goal ?? "—"}</span>
      ),
    },
    {
      key: "status",
      header: "状态",
      width: "120px",
      render: (row: TaskResponse) => <StatusPill status={row.status} />,
    },
    {
      key: "created_at",
      header: "创建时间",
      width: "190px",
      render: (row: TaskResponse) => (
        <span className={styles.muted}>{formatTime(row.created_at)}</span>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="任务控制台"
        subtitle="创建任务后自动进入执行；点击任意任务查看进度与结果。"
      />

      <div className={styles.layout}>
        <Card className={styles.formCard}>
          <h3 className={styles.cardTitle}>新建任务</h3>
          <CreateTaskForm
            onSubmit={(goal, context) => createMutation.mutate({ goal, context })}
            submitting={createMutation.isPending}
          />
        </Card>

        <Card className={styles.listCard}>
          <h3 className={styles.cardTitle}>
            任务列表{listQuery.data ? `（${listQuery.data.total}）` : ""}
          </h3>
          {listQuery.isLoading ? (
            <Spinner label="加载任务…" />
          ) : listQuery.isError ? (
            <EmptyState
              title="无法加载任务列表"
              description="请确认后端服务已启动。"
            />
          ) : listQuery.data && listQuery.data.tasks.length > 0 ? (
            <Table
              columns={columns}
              rows={listQuery.data.tasks}
              rowKey={(row) => row.task_id}
              onRowClick={(row) => navigate(`/tasks/${row.task_id}`)}
            />
          ) : (
            <EmptyState
              title="还没有任务"
              description="在左侧创建你的第一个任务。"
            />
          )}
        </Card>
      </div>
    </div>
  );
}
