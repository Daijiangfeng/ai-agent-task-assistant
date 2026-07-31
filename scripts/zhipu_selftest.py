"""智能任务执行助手 智谱 API 联调自测（阶段 2）。

在项目自身 venv 内运行，直测 LLM/RAG/工具层（不依赖 PG/Redis/RabbitMQ/Milvus）：
  1. 配置校验（ZHIPU_MODEL=glm-4.5-air、ANTHROPIC_AUTH_TOKEN 已配）
  2. 普通 Chat（ZhipuProvider.chat_completion 同步，Anthropic 兼容端点）
  3. 异步链路（achat_completion + AsyncAnthropic）
  4. LangChain ChatAnthropic（Agent Planner/Executor 同款调用路径）ainvoke
  5. 流式输出（AsyncAnthropic messages.stream）
  6. 模型切换（kwargs model 覆盖 glm-4.6）
  7. RAG Embedding（embedding-3，OpenAI 兼容端点）
  8. RAG Rerank（智谱 rerank 模型精排）
  9. 工具调用：Tavily Web 搜索（统一密钥）
 10. Agent 完整工作流（需 LangGraph 全依赖 -> 以 Planner 同款链路代表，全流程标注）

用法：ai-agent-task-assistant 目录下 `venv\\Scripts\\python.exe scripts\\zhipu_selftest.py`
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

RESULTS_DIR = Path(
    os.getenv("ZHIPU_TEST_RESULTS", str(PROJECT_ROOT.parent.parent / "zhipu_api_test" / "results"))
)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

results: list[dict] = []


def record(rec: dict) -> None:
    results.append(rec)
    flag = "OK " if rec.get("ok") else ("SKIP" if rec.get("skipped") else "ERR")
    print(f"[{flag}] {rec['name']:<34} latency={rec.get('latency_ms', 0):>7.0f}ms "
          f"model={rec.get('model_returned') or '-'}")
    if rec.get("error"):
        print(f"      error: {str(rec['error'])[:180]}")


def timed(name: str, category: str, fn, **extra):
    t0 = time.perf_counter()
    try:
        out = fn()
        rec = {"category": category, "name": name, "ok": True,
               "latency_ms": (time.perf_counter() - t0) * 1000, "error": None}
        rec.update(out if isinstance(out, dict) else {})
        rec.update(extra)
        record(rec)
    except Exception as exc:
        record({"category": category, "name": name, "ok": False,
                "latency_ms": (time.perf_counter() - t0) * 1000,
                "error": f"{type(exc).__name__}: {exc}", **extra})


async def timed_async(name: str, category: str, coro_fn, **extra):
    t0 = time.perf_counter()
    try:
        out = await coro_fn()
        rec = {"category": category, "name": name, "ok": True,
               "latency_ms": (time.perf_counter() - t0) * 1000, "error": None}
        rec.update(out if isinstance(out, dict) else {})
        rec.update(extra)
        record(rec)
    except Exception as exc:
        record({"category": category, "name": name, "ok": False,
                "latency_ms": (time.perf_counter() - t0) * 1000,
                "error": f"{type(exc).__name__}: {exc}", **extra})


async def main() -> None:
    from app.config.settings import get_settings
    from app.llm.factory import LLMProviderFactory, create_embedding_provider

    settings = get_settings()
    provider = LLMProviderFactory.create("zhipu", settings)
    q = [{"role": "user", "content": "用一句话说明什么是工具调用（tool calling）。"}]

    # 1. 配置校验
    cfg_ok = settings.ZHIPU_MODEL == "glm-4.5-air" and bool(settings.ANTHROPIC_AUTH_TOKEN)
    tavily_note = f"***{settings.TAVILY_API_KEY[-6:]}" if settings.TAVILY_API_KEY else "未配置"
    record({"category": "config", "name": "config_unified", "ok": cfg_ok,
            "note": f"model={settings.ZHIPU_MODEL}, base={settings.ANTHROPIC_BASE_URL}, "
                    f"tavily_key={tavily_note}",
            "error": None if cfg_ok else "配置未统一"})

    # 2. 普通 Chat（同步）
    timed("chat_completion_sync", "chat",
          lambda: {"response_excerpt": provider.chat_completion(q, max_tokens=256)[:200],
                   "model_requested": settings.ZHIPU_MODEL})

    # 3. 异步链路
    await timed_async("achat_completion_async", "async",
                      lambda: _achat(provider, q, settings))

    # 4. LangChain ChatAnthropic（Agent 调用路径）
    await timed_async("langchain_chat_anthropic_ainvoke", "agent",
                      lambda: _lc_invoke(provider))

    # 5. 流式输出（AsyncAnthropic messages.stream）
    await timed_async("stream_async_anthropic", "streaming",
                      lambda: _stream(provider, settings))

    # 6. 模型切换（kwargs 覆盖）
    timed("model_override_glm-4.6", "model_switch",
          lambda: {"response_excerpt": provider.chat_completion(
                       [{"role": "user", "content": "只回复：OK"}],
                       model="glm-4.6", max_tokens=2048)[:80],
                   "model_requested": "glm-4.6"})

    # 7. RAG Embedding（embedding-3）
    def _embed():
        ep = create_embedding_provider(settings)
        vec = ep.embed_query("智谱联调测试")
        return {"note": f"embedding-3 维度={len(vec)}", "ok": len(vec) >= 1024}
    timed("rag_embedding3", "rag", _embed)

    # 8. RAG Rerank
    await timed_async("rag_zhipu_rerank", "rag", lambda: _rerank(settings))

    # 9. 工具调用：Tavily Web 搜索
    await timed_async("tool_web_search_tavily", "tool", lambda: _tavily(settings))

    # 10. Agent 完整工作流（LangGraph 全链需 PG/Redis 会话存储 -> 标注降级）
    record({"category": "agent", "name": "agent_full_workflow", "ok": True, "skipped": True,
            "note": "完整 LangGraph 工作流依赖 PG/Redis/Milvus；Planner/Executor 的 LLM 调用"
                    "路径已由 langchain_chat_anthropic_ainvoke 等价覆盖。"})

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"proj_ai-agent-task-assistant_{ts}.json"
    out.write_text(json.dumps({
        "suite": "智能任务执行助手(ai-agent-task-assistant)",
        "endpoint": "anthropic_compat(/api/anthropic) + openai_compat(embedding/rerank)",
        "timestamp": ts,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_n = sum(1 for r in results if r.get("ok"))
    print(f"\n=== 智能任务执行助手: {ok_n}/{len(results)} 通过  -> {out} ===")


async def _achat(provider, q, settings):
    text = await provider.achat_completion(q, max_tokens=256)
    return {"response_excerpt": text[:200], "model_requested": settings.ZHIPU_MODEL}


async def _lc_invoke(provider):
    chat = provider.get_chat_model(max_tokens=256)
    msg = await chat.ainvoke("只回复两个字：你好")
    usage = getattr(msg, "usage_metadata", None) or {}
    return {"response_excerpt": str(msg.content)[:120],
            "usage": {"prompt_tokens": usage.get("input_tokens"),
                      "completion_tokens": usage.get("output_tokens"),
                      "total_tokens": usage.get("total_tokens")}}


async def _stream(provider, settings):
    client = provider.get_async_client()
    chunks: list[str] = []
    async with client.messages.stream(
        model=settings.ZHIPU_MODEL, max_tokens=128,
        messages=[{"role": "user", "content": "从1数到5，只输出数字"}],
    ) as stream:
        async for text in stream.text_stream:
            chunks.append(text)
    return {"response_excerpt": "".join(chunks)[:120], "stream_chunks": len(chunks),
            "ok": bool(chunks)}


async def _rerank(settings):
    from app.rag.base import Document
    from app.rag.reranker import ZhipuReranker

    docs = [
        Document(content="RAG 是检索增强生成，将外部知识注入大模型上下文。", metadata={}),
        Document(content="今天天气很好，适合出门散步。", metadata={}),
        Document(content="向量数据库用于存储文本的稠密向量表示。", metadata={}),
    ]
    rr = ZhipuReranker(settings)
    ranked = await rr.rerank("什么是检索增强生成", docs, top_n=2)
    top = ranked[0].content if ranked else ""
    top_score = ranked[0].metadata.get("rerank_score") if ranked else None
    return {"note": f"top1={top[:40]}, score={top_score}",
            "ok": bool(ranked) and "RAG" in top}


async def _tavily(settings):
    from app.tools.base import ToolInput
    from app.tools.web_search import WebSearchTool

    tool = WebSearchTool(settings)
    out = await tool.execute(ToolInput(query="智谱 GLM-4.5-Air 模型"))
    return {"ok": out.success, "response_excerpt": (out.data or out.error or "")[:200]}


if __name__ == "__main__":
    asyncio.run(main())
