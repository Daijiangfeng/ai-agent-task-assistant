/**
 * React Query 客户端与查询键。
 * 任务详情查询使用 refetchInterval，到达终态自动停轮询。
 */

import { QueryClient } from "@tanstack/react-query";
import { isStuck, isTerminal, type TaskStatusResponse } from "./types";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5_000,
      refetchOnWindowFocus: false,
    },
  },
});

export const queryKeys = {
  health: ["health"] as const,
  stats: ["stats"] as const,
  tools: ["tools"] as const,
  tasks: ["tasks"] as const,
  taskList: (limit: number, offset: number) =>
    ["tasks", "list", limit, offset] as const,
  task: (id: string) => ["tasks", "detail", id] as const,
  templates: ["templates"] as const,
  template: (id: string) => ["templates", "detail", id] as const,
  documents: ["knowledge", "documents"] as const,
};

/**
 * 任务详情轮询间隔：执行中 1.5s 一次；
 * 终态（completed/failed/cancelled）与等待人工介入
 * （paused/awaiting_approval）返回 false 停轮询。
 */
export function taskRefetchInterval(
  data: TaskStatusResponse | undefined,
): number | false {
  if (!data) return 1_500;
  return isTerminal(data.status) || isStuck(data.status) ? false : 1_500;
}
