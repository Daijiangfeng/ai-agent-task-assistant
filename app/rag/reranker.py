"""
智谱 Rerank 重排器。
调用智谱 OpenAI 兼容端点 POST /api/paas/v4/rerank，对向量召回的候选文档精排。
鉴权方式：Authorization: Bearer <ANTHROPIC_AUTH_TOKEN>
API 文档：https://docs.bigmodel.cn/api-reference/模型-api/文本重排序
"""

from __future__ import annotations

import httpx

from app.config.logging import get_logger
from app.config.settings import Settings
from app.rag.base import BaseReranker, Document

logger = get_logger(__name__)

# 智谱 Rerank API 限制：候选文档最多 128 条，query/单条文档最长 4096 字符
_MAX_DOCUMENTS = 128
_MAX_TEXT_LENGTH = 4096
_REQUEST_TIMEOUT = 30.0


class RerankError(Exception):
    """Rerank 调用失败异常，携带明确的上下文信息。"""


class ZhipuReranker(BaseReranker):
    """
    智谱 Rerank 重排器。

    通过 httpx.AsyncClient 异步调用智谱 rerank 模型（不阻塞事件循环），
    按 relevance_score 对候选文档降序重排，分数写入 metadata["rerank_score"]。
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    @property
    def _endpoint(self) -> str:
        """Rerank API 完整地址（基于 OpenAI 兼容端点基础地址）。"""
        return self._settings.ZHIPU_OPENAI_BASE_URL.rstrip("/") + "/rerank"

    def _headers(self) -> dict[str, str]:
        """构造标准 Authorization: Bearer 鉴权头。"""
        token = (self._settings.ANTHROPIC_AUTH_TOKEN or "").strip()
        if not token:
            raise RerankError(
                "ANTHROPIC_AUTH_TOKEN 未配置：智谱 Rerank API 需要该 token "
                "走 Authorization: Bearer 鉴权，请在 .env 中设置 ANTHROPIC_AUTH_TOKEN。"
            )
        return {"Authorization": f"Bearer {token}"}

    async def rerank(
        self, query: str, documents: list[Document], top_n: int
    ) -> list[Document]:
        """
        对候选文档精排。

        Args:
            query: 查询文本（超长自动截断至 API 限制）。
            documents: 向量召回的候选文档（最多取前 128 条）。
            top_n: 精排后保留的文档数。

        Returns:
            按 relevance_score 降序的 Document 列表，metadata 含 rerank_score。

        Raises:
            RerankError: 网络错误、HTTP 非 2xx 或响应格式异常。
        """
        if not documents:
            return []

        candidates = documents[:_MAX_DOCUMENTS]
        payload = {
            "model": self._settings.ZHIPU_RERANK_MODEL,
            "query": query[:_MAX_TEXT_LENGTH],
            "documents": [d.content[:_MAX_TEXT_LENGTH] for d in candidates],
            "top_n": top_n,
        }

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.post(
                    self._endpoint, json=payload, headers=self._headers()
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as e:
            raise RerankError(
                f"Rerank API 返回错误状态 {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.HTTPError as e:
            raise RerankError(f"Rerank API 请求失败: {e}") from e
        except ValueError as e:
            raise RerankError(f"Rerank API 响应不是合法 JSON: {e}") from e

        results = body.get("results")
        if not isinstance(results, list):
            raise RerankError(f"Rerank API 响应缺少 results 字段: {str(body)[:200]}")

        reranked: list[Document] = []
        for item in results:
            index = item.get("index")
            score = item.get("relevance_score")
            if index is None or not (0 <= index < len(candidates)):
                logger.warning("Rerank 结果 index 越界，已跳过", index=index)
                continue
            doc = candidates[index]
            metadata = dict(doc.metadata)
            metadata["rerank_score"] = score
            reranked.append(Document(content=doc.content, metadata=metadata))

        # 按 relevance_score 降序（API 通常已排序，此处兜底保证顺序）
        reranked.sort(
            key=lambda d: d.metadata.get("rerank_score") or 0.0, reverse=True
        )
        logger.info(
            "Rerank 完成",
            candidate_count=len(candidates),
            returned_count=len(reranked),
            top_n=top_n,
        )
        return reranked[:top_n]
