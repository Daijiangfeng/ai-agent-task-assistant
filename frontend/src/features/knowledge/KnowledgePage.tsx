import { useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../../lib/apiClient";
import { queryKeys } from "../../lib/queryClient";
import {
  Button,
  Card,
  EmptyState,
  Field,
  PageHeader,
  Spinner,
  Table,
  useToast,
} from "../../components";
import type { DocumentInfo, KnowledgeSearchResult } from "../../lib/types";
import styles from "./KnowledgePage.module.css";

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

export default function KnowledgePage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const [ingestPath, setIngestPath] = useState("");
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(5);
  const [results, setResults] = useState<KnowledgeSearchResult[] | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const documentsQuery = useQuery({
    queryKey: queryKeys.documents,
    queryFn: api.knowledge.listDocuments,
  });

  const refreshDocuments = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.documents });

  const ingestMutation = useMutation({
    mutationFn: (path: string) => api.knowledge.ingest(path),
    onSuccess: (res) => {
      toast.notify(`已索引 ${res.chunks_indexed} 个分块`, "success");
      setIngestPath("");
      refreshDocuments();
    },
    onError: (err) => toast.notify(errorText(err, "入库失败"), "error"),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.knowledge.upload(file),
    onSuccess: (res) => {
      toast.notify(`已上传并索引 ${res.chunks_indexed} 个分块`, "success");
      if (fileInputRef.current) fileInputRef.current.value = "";
      refreshDocuments();
    },
    onError: (err) => toast.notify(errorText(err, "上传失败"), "error"),
  });

  const searchMutation = useMutation({
    mutationFn: (vars: { query: string; topK: number }) =>
      api.knowledge.search(vars.query, vars.topK),
    onSuccess: (res) => setResults(res.results),
    onError: (err) => toast.notify(errorText(err, "检索失败"), "error"),
  });

  const deleteMutation = useMutation({
    mutationFn: (source: string) => api.knowledge.deleteDocument(source),
    onSuccess: (res) => {
      toast.notify(`已删除 ${res.chunks_deleted} 个分块`, "success");
      refreshDocuments();
    },
    onError: (err) => toast.notify(errorText(err, "删除失败"), "error"),
  });

  function handleIngest(event: FormEvent) {
    event.preventDefault();
    if (!ingestPath.trim()) {
      toast.notify("请输入服务端文件路径", "error");
      return;
    }
    ingestMutation.mutate(ingestPath.trim());
  }

  function handleUpload(event: FormEvent) {
    event.preventDefault();
    const file = fileInputRef.current?.files?.[0];
    if (!file) {
      toast.notify("请选择要上传的文件", "error");
      return;
    }
    uploadMutation.mutate(file);
  }

  function handleSearch(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) {
      toast.notify("请输入检索内容", "error");
      return;
    }
    searchMutation.mutate({ query: query.trim(), topK });
  }

  const docColumns = [
    {
      key: "source",
      header: "来源",
      render: (row: DocumentInfo) => (
        <span className={styles.docSource}>{row.source}</span>
      ),
    },
    {
      key: "type",
      header: "类型",
      width: "120px",
      render: (row: DocumentInfo) => row.type ?? "—",
    },
    {
      key: "chunk_count",
      header: "分块数",
      width: "100px",
      align: "right" as const,
      render: (row: DocumentInfo) => row.chunk_count,
    },
    {
      key: "actions",
      header: "",
      width: "90px",
      align: "right" as const,
      render: (row: DocumentInfo) => (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => deleteMutation.mutate(row.source)}
          disabled={deleteMutation.isPending}
        >
          删除
        </Button>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="知识库"
        subtitle="录入文档、检索验证与文档管理，为 RAG 检索提供语料。"
      />

      <div className={styles.grid}>
        <Card className={styles.card}>
          <h3 className={styles.cardTitle}>路径入库</h3>
          <form className={styles.form} onSubmit={handleIngest}>
            <Field
              label="服务端文件路径"
              name="ingestPath"
              value={ingestPath}
              onChange={(e) => setIngestPath(e.target.value)}
              placeholder="例如：/data/docs/handbook.md"
              disabled={ingestMutation.isPending}
            />
            <Button type="submit" loading={ingestMutation.isPending}>
              入库
            </Button>
          </form>
        </Card>

        <Card className={styles.card}>
          <h3 className={styles.cardTitle}>上传文件</h3>
          <form className={styles.form} onSubmit={handleUpload}>
            <input
              ref={fileInputRef}
              type="file"
              className={styles.file}
              disabled={uploadMutation.isPending}
            />
            <Button type="submit" loading={uploadMutation.isPending}>
              上传并索引
            </Button>
          </form>
        </Card>
      </div>

      <Card className={styles.card}>
        <h3 className={styles.cardTitle}>检索验证</h3>
        <form className={styles.searchForm} onSubmit={handleSearch}>
          <Field
            pill
            name="query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入问题以检索相关分块…"
            disabled={searchMutation.isPending}
          />
          <Field
            type="number"
            name="topK"
            value={topK}
            min={1}
            max={20}
            onChange={(e) => setTopK(Number(e.target.value) || 5)}
            className={styles.topK}
            disabled={searchMutation.isPending}
          />
          <Button type="submit" loading={searchMutation.isPending}>
            检索
          </Button>
        </form>

        {searchMutation.isPending ? (
          <Spinner label="检索中…" />
        ) : results && results.length > 0 ? (
          <div className={styles.results}>
            {results.map((r, i) => (
              <div key={i} className={styles.result}>
                <div className={styles.resultMeta}>
                  <span className={styles.score}>
                    相关度 {r.score != null ? r.score.toFixed(3) : "—"}
                  </span>
                  <span className={styles.resultSource}>
                    {String(r.metadata?.source ?? "未知来源")}
                  </span>
                </div>
                <p className={styles.resultContent}>{r.content}</p>
              </div>
            ))}
          </div>
        ) : results && results.length === 0 ? (
          <EmptyState title="没有匹配的结果" description="换个说法或先录入文档。" />
        ) : null}
      </Card>

      <Card className={styles.card}>
        <h3 className={styles.cardTitle}>
          已索引文档{documentsQuery.data ? `（${documentsQuery.data.total}）` : ""}
        </h3>
        {documentsQuery.isLoading ? (
          <Spinner label="加载文档…" />
        ) : documentsQuery.isError ? (
          <EmptyState title="无法加载文档列表" description="请确认后端服务已启动。" />
        ) : documentsQuery.data && documentsQuery.data.documents.length > 0 ? (
          <Table
            columns={docColumns}
            rows={documentsQuery.data.documents}
            rowKey={(row) => row.source}
          />
        ) : (
          <EmptyState title="知识库为空" description="通过上方入库或上传添加文档。" />
        )}
      </Card>
    </div>
  );
}
