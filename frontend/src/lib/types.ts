/**
 * 后端 Schema 的 TypeScript 镜像。
 * 与 app/models/api_schemas.py、task.py、plan.py 保持一致。
 */

export type TaskStatus =
  | "pending"
  | "planning"
  | "executing"
  | "reflecting"
  | "replanning"
  | "awaiting_approval"
  | "paused"
  | "cancelled"
  | "completed"
  | "failed";

export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface ApprovalRequest {
  id: string;
  task_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  reason: string;
  status: ApprovalStatus;
  created_at: string;
  decided_at: string | null;
  decision_note: string | null;
  modified_args: Record<string, unknown> | null;
}

export interface AgentResult {
  role: string;
  agent_name: string | null;
  objective: string | null;
  result: string | null;
  status: string;
  error: string | null;
  latency_ms: number | null;
}

export interface SubTask {
  id: string;
  description: string;
  status: TaskStatus;
  result: string | null;
  tool_used: string | null;
  error: string | null;
  dependencies: string[];
}

export interface Plan {
  goal: string;
  subtasks: SubTask[];
  version: number;
  reasoning: string | null;
}

export interface ReflectionResult {
  is_satisfactory: boolean;
  accuracy_score: number;
  completeness_score: number;
  relevance_score: number;
  issues: string[];
  suggestion: string | null;
}

export interface TaskResponse {
  task_id: string;
  status: TaskStatus;
  plan: Plan | null;
  created_at: string;
}

export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  current_step: string | null;
  progress: number;
  plan: Plan | null;
  subtasks: SubTask[];
  reflection: ReflectionResult | null;
  iteration_count: number;
  plan_version: number;
  execution_mode: string | null;
  agent_results: AgentResult[];
  pending_approval: ApprovalRequest | null;
  approval_history: ApprovalRequest[];
  error: string | null;
  final_result: string | null;
}

export interface AgentTemplate {
  id: string;
  name: string;
  description: string | null;
  category: string;
  goal_template: string;
  context_template: string | null;
  tags: string[];
  variables: string[];
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

export interface TemplateListResponse {
  total: number;
  templates: AgentTemplate[];
}

export interface TemplateRunRequest {
  inputs: Record<string, string>;
  auto_execute: boolean;
}

export interface TaskListResponse {
  total: number;
  tasks: TaskResponse[];
}

export interface HealthResponse {
  status: string;
  version: string;
}

export interface IngestDocumentResponse {
  source: string;
  chunks_indexed: number;
}

export interface KnowledgeSearchResult {
  content: string;
  metadata: Record<string, unknown>;
  score: number | null;
}

export interface KnowledgeSearchResponse {
  query: string;
  results: KnowledgeSearchResult[];
}

export interface DocumentInfo {
  source: string;
  type: string | null;
  chunk_count: number;
}

export interface DocumentListResponse {
  total: number;
  documents: DocumentInfo[];
}

export interface DeleteDocumentResponse {
  source: string;
  chunks_deleted: number;
}

export interface ToolInfo {
  name: string;
  description: string;
}

export interface ToolListResponse {
  total: number;
  tools: ToolInfo[];
}

export interface StatsResponse {
  version: string;
  task_total: number;
  tasks_by_status: Record<string, number>;
  tool_count: number;
  knowledge_document_count: number;
  knowledge_chunk_count: number;
}

/** 终态：到达后停止轮询。 */
export const TERMINAL_STATUSES: TaskStatus[] = [
  "completed",
  "failed",
  "cancelled",
];

/** 等待人工介入或已暂停：无需自动轮询（用户操作后手动刷新）。 */
export const STUCK_STATUSES: TaskStatus[] = [
  "paused",
  "awaiting_approval",
];

export function isTerminal(status: TaskStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export function isStuck(status: TaskStatus): boolean {
  return STUCK_STATUSES.includes(status);
}
