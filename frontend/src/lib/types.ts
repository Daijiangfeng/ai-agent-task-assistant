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
  | "completed"
  | "failed";

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
  error: string | null;
  final_result: string | null;
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
export const TERMINAL_STATUSES: TaskStatus[] = ["completed", "failed"];

export function isTerminal(status: TaskStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}
