import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusPill } from "./StatusPill";
import type { TaskStatus } from "../lib/types";

const CASES: Array<[TaskStatus, string]> = [
  ["pending", "待执行"],
  ["planning", "规划中"],
  ["executing", "执行中"],
  ["reflecting", "反思中"],
  ["replanning", "重规划"],
  ["awaiting_approval", "待审批"],
  ["paused", "已暂停"],
  ["cancelled", "已取消"],
  ["completed", "已完成"],
  ["failed", "失败"],
];

describe("StatusPill", () => {
  it.each(CASES)("状态 %s 渲染中文标签且带 data-status", (status, label) => {
    const { container } = render(<StatusPill status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(container.querySelector(`[data-status="${status}"]`)).not.toBeNull();
  });
});
