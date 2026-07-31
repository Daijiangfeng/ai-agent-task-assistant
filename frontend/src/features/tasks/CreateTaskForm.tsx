import { useState, type FormEvent } from "react";
import { Button, Field, TextArea } from "../../components";
import styles from "./CreateTaskForm.module.css";

interface Props {
  onSubmit: (goal: string, context: string) => void;
  submitting?: boolean;
}

/** 任务创建表单：goal 必填，context 可选；提交中禁用按钮。 */
export function CreateTaskForm({ onSubmit, submitting = false }: Props) {
  const [goal, setGoal] = useState("");
  const [context, setContext] = useState("");
  const [error, setError] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!goal.trim()) {
      setError("请输入任务目标");
      return;
    }
    setError("");
    onSubmit(goal.trim(), context.trim());
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit} aria-label="创建任务">
      <Field
        label="任务目标"
        name="goal"
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
        error={error}
        placeholder="例如：调研三家主流向量数据库并给出选型建议"
        disabled={submitting}
      />
      <TextArea
        label="补充上下文（可选）"
        name="context"
        value={context}
        onChange={(e) => setContext(e.target.value)}
        rows={3}
        placeholder="补充背景、约束或偏好，便于 Agent 规划"
        disabled={submitting}
      />
      <div className={styles.actions}>
        <Button type="submit" loading={submitting}>
          创建并执行
        </Button>
      </div>
    </form>
  );
}
