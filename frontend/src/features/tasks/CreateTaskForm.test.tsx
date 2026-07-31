import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CreateTaskForm } from "./CreateTaskForm";

describe("CreateTaskForm", () => {
  it("目标为空时阻止提交并提示错误", async () => {
    const onSubmit = vi.fn();
    render(<CreateTaskForm onSubmit={onSubmit} />);

    await userEvent.click(screen.getByRole("button", { name: "创建并执行" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText("请输入任务目标")).toBeInTheDocument();
  });

  it("填写目标后提交并回传去空白后的值", async () => {
    const onSubmit = vi.fn();
    render(<CreateTaskForm onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText("任务目标"), "  调研向量库  ");
    await userEvent.type(screen.getByLabelText("补充上下文（可选）"), " 预算有限 ");
    await userEvent.click(screen.getByRole("button", { name: "创建并执行" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("调研向量库", "预算有限");
  });

  it("提交中禁用按钮", () => {
    render(<CreateTaskForm onSubmit={vi.fn()} submitting />);
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
