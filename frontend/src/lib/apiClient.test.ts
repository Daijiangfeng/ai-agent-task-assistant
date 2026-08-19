import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./apiClient";

function mockFetch(response: {
  ok: boolean;
  status?: number;
  json?: unknown;
  statusText?: string;
}) {
  return vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status ?? (response.ok ? 200 : 400),
    statusText: response.statusText ?? "",
    json: async () => response.json,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiClient", () => {
  it("在 basePath 下发起 GET 请求并解析 JSON", async () => {
    const fetchMock = mockFetch({ ok: true, json: { status: "ok", version: "1" } });
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.stats.get();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/stats");
    expect(result).toEqual({ status: "ok", version: "1" });
  });

  it("创建任务时保留 /tasks/ 尾斜杠避免 307", async () => {
    const fetchMock = mockFetch({ ok: true, json: { task_id: "t1" } });
    vi.stubGlobal("fetch", fetchMock);

    await api.tasks.create("目标", "上下文");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/tasks/");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ goal: "目标", context: "上下文" });
  });

  it("非 2xx 响应抛出 ApiError 并携带 detail", async () => {
    const fetchMock = mockFetch({ ok: false, status: 404, json: { detail: "任务不存在" } });
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.tasks.get("missing")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      detail: "任务不存在",
    });
  });

  it("网络异常抛出 ApiError(status=0)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("failed")));

    const error = await api.health().catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
  });
});
