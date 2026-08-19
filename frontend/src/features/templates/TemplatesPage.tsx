import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../../lib/apiClient";
import { queryKeys } from "../../lib/queryClient";
import {
  Button,
  Card,
  EmptyState,
  Field,
  PageHeader,
  Spinner,
  TextArea,
  useToast,
} from "../../components";
import type { AgentTemplate } from "../../lib/types";
import styles from "./TemplatesPage.module.css";

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

const CATEGORY_LABELS: Record<string, string> = {
  market_research: "市场调研",
  document_analysis: "文档分析",
  code_review: "代码审查",
  general: "通用任务",
};

export default function TemplatesPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({
    queryKey: queryKeys.templates,
    queryFn: () => api.templates.list(),
  });

  const [selected, setSelected] = useState<AgentTemplate | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [autoExecute, setAutoExecute] = useState(true);
  const [creating, setCreating] = useState(false);

  const runMutation = useMutation({
    mutationFn: (vars: { template: AgentTemplate; inputs: Record<string, string> }) =>
      api.templates.run(vars.template.id, {
        inputs: vars.inputs,
        auto_execute: autoExecute,
      }),
    onSuccess: (task) => {
      toast.notify("已基于模板创建任务", "success");
      navigate(`/tasks/${task.task_id}`);
    },
    onError: (err) => toast.notify(errorText(err, "创建任务失败"), "error"),
  });

  const deleteMutation = useMutation({
    mutationFn: (templateId: string) => api.templates.remove(templateId),
    onSuccess: () => {
      toast.notify("模板已删除", "success");
      queryClient.invalidateQueries({ queryKey: queryKeys.templates });
    },
    onError: (err) => toast.notify(errorText(err, "删除失败"), "error"),
  });

  function selectTemplate(template: AgentTemplate) {
    setSelected(template);
    setInputs(Object.fromEntries(template.variables.map((v) => [v, ""])));
  }

  return (
    <div>
      <PageHeader title="任务模板" subtitle="内置 Agent Skill 与自定义模板，一键生成任务" />

      <Card className={styles.section}>
        <div className={styles.sectionHead}>
          <h3 className={styles.sectionTitle}>新建模板</h3>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setCreating((v) => !v)}
          >
            {creating ? "收起" : "展开"}
          </Button>
        </div>
        {creating ? (
          <CreateTemplateForm
            onDone={() => {
              setCreating(false);
              queryClient.invalidateQueries({ queryKey: queryKeys.templates });
            }}
          />
        ) : null}
      </Card>

      {listQuery.isLoading ? (
        <div className={styles.center}>
          <Spinner label="加载模板…" />
        </div>
      ) : listQuery.isError || !listQuery.data ? (
        <EmptyState
          title="模板加载失败"
          description="请确认后端服务已启动。"
        />
      ) : listQuery.data.templates.length === 0 ? (
        <EmptyState title="暂无模板" description="点击上方新建一个自定义模板。" />
      ) : (
        <div className={styles.grid}>
          {listQuery.data.templates.map((template) => (
            <Card key={template.id} className={styles.templateCard}>
              <div className={styles.templateHead}>
                <span className={styles.templateName}>{template.name}</span>
                {template.is_builtin ? (
                  <span className={styles.builtin}>内置</span>
                ) : null}
              </div>
              <span className={styles.category}>
                {CATEGORY_LABELS[template.category] ?? template.category}
              </span>
              {template.description ? (
                <p className={styles.description}>{template.description}</p>
              ) : null}
              <p className={styles.goalPreview}>
                目标：{template.goal_template}
              </p>
              {template.tags.length > 0 ? (
                <div className={styles.tags}>
                  {template.tags.map((tag) => (
                    <span key={tag} className={styles.tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}
              <div className={styles.templateActions}>
                <Button
                  size="sm"
                  onClick={() => selectTemplate(template)}
                >
                  使用
                </Button>
                {!template.is_builtin ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    loading={deleteMutation.isPending}
                    onClick={() => {
                      if (window.confirm(`删除模板「${template.name}」？`)) {
                        deleteMutation.mutate(template.id);
                      }
                    }}
                  >
                    删除
                  </Button>
                ) : null}
              </div>
            </Card>
          ))}
        </div>
      )}

      {selected ? (
        <Card className={styles.section}>
          <h3 className={styles.sectionTitle}>
            使用模板：{selected.name}
            <button
              className={styles.close}
              onClick={() => setSelected(null)}
              aria-label="关闭模板使用面板"
            >
              ×
            </button>
          </h3>
          <form
            className={styles.runForm}
            onSubmit={(e) => {
              e.preventDefault();
              runMutation.mutate({ template: selected, inputs });
            }}
            aria-label="使用模板"
          >
            {selected.variables.length > 0 ? (
              <div className={styles.inputs}>
                {selected.variables.map((variable) => (
                  <Field
                    key={variable}
                    label={variable}
                    name={variable}
                    value={inputs[variable] ?? ""}
                    onChange={(e) =>
                      setInputs((prev) => ({
                        ...prev,
                        [variable]: e.target.value,
                      }))
                    }
                    placeholder={`输入 ${variable}`}
                  />
                ))}
              </div>
            ) : (
              <p className={styles.noVariables}>该模板无需变量。</p>
            )}
            <label className={styles.checkbox}>
              <input
                type="checkbox"
                checked={autoExecute}
                onChange={(e) => setAutoExecute(e.target.checked)}
              />
              创建后立即执行
            </label>
            <div className={styles.runActions}>
              <Button type="submit" loading={runMutation.isPending}>
                创建任务
              </Button>
              <Button
                variant="ghost"
                type="button"
                onClick={() => setSelected(null)}
              >
                取消
              </Button>
            </div>
          </form>
        </Card>
      ) : null}
    </div>
  );
}

interface CreateTemplateFormProps {
  onDone: () => void;
}

function CreateTemplateForm({ onDone }: CreateTemplateFormProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("general");
  const [goalTemplate, setGoalTemplate] = useState("");
  const [contextTemplate, setContextTemplate] = useState("");
  const [tags, setTags] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      api.templates.create({
        name: name.trim(),
        description: description.trim() || undefined,
        category,
        goal_template: goalTemplate.trim(),
        context_template: contextTemplate.trim() || undefined,
        tags: tags
          .split(/[,，]/)
          .map((t) => t.trim())
          .filter(Boolean),
      }),
    onSuccess: () => {
      toast.notify("模板已创建", "success");
      queryClient.invalidateQueries({ queryKey: queryKeys.templates });
      onDone();
    },
    onError: (err) => toast.notify(errorText(err, "创建模板失败"), "error"),
  });

  return (
    <form
      className={styles.createForm}
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim() || !goalTemplate.trim()) {
          toast.notify("名称与目标模板为必填项", "error");
          return;
        }
        createMutation.mutate();
      }}
      aria-label="新建模板"
    >
      <div className={styles.inputs}>
        <Field
          label="模板名称 *"
          name="template-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如：竞品分析"
        />
        <Field
          label="类别"
          name="template-category"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          placeholder="general"
        />
        <Field
          label="描述"
          name="template-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="模板用途说明"
        />
        <Field
          label="标签（逗号分隔）"
          name="template-tags"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          placeholder="调研, 分析"
        />
      </div>
      <TextArea
        label="目标模板 *（{var} 占位符）"
        name="template-goal"
        value={goalTemplate}
        onChange={(e) => setGoalTemplate(e.target.value)}
        rows={2}
        placeholder="分析 {company} 的核心竞争力与市场地位"
      />
      <TextArea
        label="上下文模板（可选）"
        name="template-context"
        value={contextTemplate}
        onChange={(e) => setContextTemplate(e.target.value)}
        rows={2}
        placeholder="语言：{language}；输出格式：报告"
      />
      <div className={styles.runActions}>
        <Button type="submit" loading={createMutation.isPending}>
          保存模板
        </Button>
      </div>
    </form>
  );
}