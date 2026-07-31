/**
 * 单一 fetch 封装 + 领域命名空间 API。
 * 统一 basePath、JSON 序列化、错误抛出与超时；严格保留 /tasks/ 尾斜杠避免 307。
 */

import type {
  DeleteDocumentResponse,
  DocumentListResponse,
  HealthResponse,
  IngestDocumentResponse,
  KnowledgeSearchResponse,
  StatsResponse,
  TaskListResponse,
  TaskResponse,
  TaskStatusResponse,
  ToolListResponse,
} from "./types";

const BASE_PATH = "/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** multipart 场景下直接传 FormData，此时不设置 JSON 头。 */
  formData?: FormData;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** 是否带上 basePath。健康检查等根路径接口置 false。 */
  absolute?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = "GET",
    body,
    formData,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    absolute = false,
  } = options;

  const url = absolute ? path : `${BASE_PATH}${path}`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (formData) {
    payload = formData;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: payload,
      signal: options.signal ?? controller.signal,
    });
  } catch (err) {
    clearTimeout(timeout);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, "请求超时或已取消");
    }
    throw new ApiError(0, "网络错误：无法连接后端服务");
  }
  clearTimeout(timeout);

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data?.detail ?? detail;
    } catch {
      // 响应体非 JSON，沿用 statusText。
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  request,

  health: () => request<HealthResponse>("/health", { absolute: true }),

  stats: {
    get: () => request<StatsResponse>("/stats"),
  },

  tools: {
    list: () => request<ToolListResponse>("/tools"),
  },

  tasks: {
    // 注意：尾斜杠必须保留，否则 FastAPI 会 307 重定向丢失请求体。
    create: (goal: string, context?: string) =>
      request<TaskResponse>("/tasks/", {
        method: "POST",
        body: { goal, context: context || null },
      }),
    list: (limit = 20, offset = 0) =>
      request<TaskListResponse>(`/tasks/?limit=${limit}&offset=${offset}`),
    get: (taskId: string) => request<TaskStatusResponse>(`/tasks/${taskId}`),
    execute: (taskId: string) =>
      request<TaskResponse>(`/tasks/${taskId}/execute`, { method: "POST" }),
  },

  knowledge: {
    ingest: (filePath: string) =>
      request<IngestDocumentResponse>("/knowledge/documents", {
        method: "POST",
        body: { file_path: filePath },
      }),
    upload: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<IngestDocumentResponse>("/knowledge/upload", {
        method: "POST",
        formData: form,
        timeoutMs: 120_000,
      });
    },
    search: (query: string, topK?: number) =>
      request<KnowledgeSearchResponse>("/knowledge/search", {
        method: "POST",
        body: { query, top_k: topK ?? null },
      }),
    listDocuments: () =>
      request<DocumentListResponse>("/knowledge/documents"),
    deleteDocument: (source: string) =>
      request<DeleteDocumentResponse>(
        `/knowledge/documents?source=${encodeURIComponent(source)}`,
        { method: "DELETE" },
      ),
  },
};
