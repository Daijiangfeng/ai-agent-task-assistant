"""
pgvector 向量库实现（生产后端）。

基于 PostgreSQL + pgvector 扩展 + HNSW 索引，支持多实例共享同一数据库，
避免 Chroma 单机持久化在多 Pod 部署下的数据竞争问题。

表结构（单表多 collection 分区）：
    vector_embeddings(collection, id, document, metadata JSONB, embedding halfvec)
    PRIMARY KEY (collection, id) + HNSW cosine 索引

注意：
- 依赖 PostgreSQL 已安装 vector 扩展（CREATE EXTENSION IF NOT EXISTS vector）；
- 需要真实 PostgreSQL 服务，仅配置 VECTOR_STORE_BACKEND=pgvector 时启用；
- 所有操作均为同步 psycopg 调用（与 Chroma 封装保持一致）；
- 维度 > 2000 时使用 halfvec（float16）存储：pgvector 的 HNSW 索引对
  vector（float32）上限为 2000 维，halfvec 上限为 4000 维（GLM 嵌入为 2048 维）。
"""

from __future__ import annotations

import threading
from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.memory.vector_store import BaseVectorStore

logger = get_logger(__name__)

TABLE_NAME = "vector_embeddings"


class PgVectorStore(BaseVectorStore):
    """
    pgvector 向量库实现。

    连接惰性建立（首次操作时），元数据以 JSONB 存储，
    支持 where 等值过滤（metadata->>'field' = value），
    供长期记忆的租户/用户隔离过滤使用。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._dim = self._settings.EMBEDDING_DIM
        self._conninfo = self._settings.postgres_dsn
        self._pool = None
        self._lock = threading.Lock()

    # ---- 连接管理 ----

    def _ensure_pool(self):
        """惰性建立连接池并初始化表结构（线程安全）。"""
        if self._pool is not None:
            return
        with self._lock:
            if self._pool is not None:
                return
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                conninfo=self._conninfo,
                min_size=1,
                max_size=10,
                open=False,
                kwargs={"connect_timeout": self._settings.DB_CONNECT_TIMEOUT},
            )
            pool.open()
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    cur.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                            collection TEXT NOT NULL,
                            id TEXT NOT NULL,
                            document TEXT NOT NULL,
                            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            embedding halfvec({self._dim}) NOT NULL,
                            PRIMARY KEY (collection, id)
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE INDEX IF NOT EXISTS ix_{TABLE_NAME}_hnsw
                        ON {TABLE_NAME} USING hnsw (embedding halfvec_cosine_ops)
                        """
                    )
            self._pool = pool
            logger.info(
                "PgVectorStore 初始化完成",
                host=self._settings.POSTGRES_HOST,
                dim=self._dim,
            )

    def _connection(self):
        self._ensure_pool()
        return self._pool.connection()

    # ---- BaseVectorStore 实现 ----

    def add(
        self,
        collection_name: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        if not ids:
            return
        metadatas = metadatas or [{} for _ in ids]
        with self._connection() as conn:
            with conn.cursor() as cur:
                for i, doc_id in enumerate(ids):
                    cur.execute(
                        f"""
                        INSERT INTO {TABLE_NAME}
                            (collection, id, document, metadata, embedding)
                        VALUES (%s, %s, %s, %s, %s::halfvec)
                        ON CONFLICT (collection, id) DO UPDATE SET
                            document = EXCLUDED.document,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding
                        """,
                        (
                            collection_name,
                            doc_id,
                            documents[i],
                            self._json(metadatas[i]),
                            self._halfvec_str(embeddings[i]),
                        ),
                    )
                conn.commit()

    def get(
        self,
        collection_name: str,
        ids: list[str],
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        sql = (
            f"SELECT id, document, metadata FROM {TABLE_NAME} "
            f"WHERE collection = %s AND id = ANY(%s)"
        )
        params: list[Any] = [collection_name, ids]
        sql += self._where_sql(where, params)
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return self._rows_to_items(cur.fetchall())

    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            f"SELECT id, document, metadata, 1 - (embedding <=> %s::halfvec) AS score "
            f"FROM {TABLE_NAME} WHERE collection = %s"
        )
        params: list[Any] = [self._halfvec_str(query_embedding), collection_name]
        sql += self._where_sql(where, params)
        sql += " ORDER BY embedding <=> %s::halfvec LIMIT %s"
        params.extend([self._halfvec_str(query_embedding), top_k])

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            doc_id, document, metadata, score = row
            items.append(
                {
                    "id": doc_id,
                    "document": document,
                    "metadata": metadata or {},
                    "score": float(score) if score is not None else 0.0,
                }
            )
        return items

    def delete(self, collection_name: str, ids: list[str]) -> None:
        if not ids:
            return
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {TABLE_NAME} WHERE collection = %s AND id = ANY(%s)",
                    (collection_name, ids),
                )
                conn.commit()

    def count(self, collection_name: str) -> int:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT count(*) FROM {TABLE_NAME} WHERE collection = %s",
                    (collection_name,),
                )
                return int(cur.fetchone()[0])

    def list_records(
        self,
        collection_name: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = (
            f"SELECT id, document, metadata FROM {TABLE_NAME} "
            f"WHERE collection = %s ORDER BY id"
        )
        params: list[Any] = [collection_name]
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return self._rows_to_items(cur.fetchall())

    def delete_by_source(self, collection_name: str, source: str) -> int:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {TABLE_NAME} "
                    f"WHERE collection = %s AND metadata->>'source' = %s",
                    (collection_name, source),
                )
                conn.commit()
                return cur.rowcount or 0

    # ---- 工具方法 ----

    @staticmethod
    def _halfvec_str(values: list[float]) -> str:
        """将浮点列表序列化为 halfvec 字面量文本（如 [1.0, 2.0, ...]）。"""
        return "[" + ",".join(repr(float(v)) for v in values) + "]"

    @staticmethod
    def _json(data: dict[str, Any]) -> str:
        """JSONB 参数序列化（psycopg 无原生 dict 适配时使用）。"""
        import json

        return json.dumps(data, ensure_ascii=False)

    def _where_sql(self, where: dict[str, Any] | None, params: list[Any]) -> str:
        """
        将等值 where 条件转换为 metadata->>'field' = %s 子句（AND 连接）。

        仅支持扁平等值字典（如 {"user_id": "u1", "tenant_id": "t1"}）。
        """
        if not where:
            return ""
        clauses = []
        for field, value in where.items():
            params.append(str(value))
            clauses.append(f"metadata->>'{field}' = %s")
        return " AND " + " AND ".join(clauses)

    @staticmethod
    def _rows_to_items(rows: list[tuple]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in rows:
            doc_id, document, metadata = row
            items.append(
                {
                    "id": doc_id,
                    "document": document,
                    "metadata": metadata or {},
                }
            )
        return items
