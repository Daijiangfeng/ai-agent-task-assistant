import { describe, expect, it } from "vitest";
import type { TaskStatusResponse } from "./types";
import { taskRefetchInterval } from "./queryClient";

function makeTask(status: TaskStatusResponse["status"]): TaskStatusResponse {
  return {
    task_id: "t1",
    status,
    current_step: null,
    progress: 0,
    plan: null,
    subtasks: [],
    reflection: null,
    iteration_count: 0,
    plan_version: 1,
    execution_mode: null,
    agent_results: [],
    pending_approval: null,
    approval_history: [],
    error: null,
    final_result: null,
  };
}

describe("taskRefetchInterval", () => {
  it("无数据时按默认间隔轮询", () => {
    expect(taskRefetchInterval(undefined)).toBe(1_500);
  });

  it("执行中状态持续轮询", () => {
    expect(taskRefetchInterval(makeTask("executing"))).toBe(1_500);
    expect(taskRefetchInterval(makeTask("planning"))).toBe(1_500);
  });

  it("到达终态停止轮询", () => {
    expect(taskRefetchInterval(makeTask("completed"))).toBe(false);
    expect(taskRefetchInterval(makeTask("failed"))).toBe(false);
    expect(taskRefetchInterval(makeTask("cancelled"))).toBe(false);
  });

  it("等待人工介入或暂停时停止轮询", () => {
    expect(taskRefetchInterval(makeTask("awaiting_approval"))).toBe(false);
    expect(taskRefetchInterval(makeTask("paused"))).toBe(false);
  });
});
