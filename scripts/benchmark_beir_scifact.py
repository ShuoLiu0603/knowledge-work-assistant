from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "apps" / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.core.config import get_settings
from app.evaluation.ir_metrics import compute_ir_metrics
from app.rag.advanced_retrieval import (
    RetrievalCandidate,
    bm25_query_terms,
    fuse_candidates,
    keyword_search_text,
    matched_terms,
    rank_bm25_rows,
)
from app.rag.embeddings import get_embedding_provider
from app.rag.retrieval import RetrievedChunk
from app.rag.vector_store import embedding_text


DATASET_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
DATASET_MD5 = "5f7d1de60b170fc8027bb7898e2efca1"
DATASET_HOMEPAGE = "https://github.com/allenai/scifact"
BEIR_HOMEPAGE = "https://github.com/beir-cellar/beir"
BEIR_PAPER = "https://arxiv.org/abs/2104.08663"
SCIFACT_PAPER = "https://aclanthology.org/2020.emnlp-main.609/"
DEFAULT_DATA_DIR = ROOT / ".run" / "benchmarks" / "beir-scifact"
DEFAULT_JSON_OUTPUT = ROOT / ".run" / "benchmarks" / "beir-scifact-results.json"
DEFAULT_MARKDOWN_OUTPUT = ROOT / ".run" / "benchmarks" / "beir-scifact-report.md"
CUTOFFS = (1, 5, 10)


@dataclass(frozen=True)
class BenchmarkDocument:
    id: str
    file_name: str


@dataclass(frozen=True)
class BenchmarkChunk:
    id: str
    document_id: str
    content: str
    title_path: str | None
    section_name: str | None = None


@dataclass(frozen=True)
class CorpusRow:
    document: BenchmarkDocument
    chunk: BenchmarkChunk
    embedding_input: str
    lexical_input: str


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Benchmark the project's Dense, BM25 and RRF retrieval on BEIR/SciFact."
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument("--embedding-workers", type=int, default=8)
    parser.add_argument("--route-limit", type=int, default=settings.retrieval_route_limit)
    parser.add_argument("--max-queries", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.embedding_workers < 1:
        raise ValueError("--embedding-workers must be positive")
    if args.route_limit < max(CUTOFFS):
        raise ValueError(f"--route-limit must be at least {max(CUTOFFS)}")

    dataset_root = ensure_dataset(args.data_dir)
    corpus = load_corpus(dataset_root / "corpus.jsonl")
    queries = load_queries(dataset_root / "queries.jsonl")
    qrels = load_qrels(dataset_root / "qrels" / "test.tsv")
    query_ids = list(qrels)
    if args.max_queries > 0:
        query_ids = query_ids[: args.max_queries]
        qrels = {query_id: qrels[query_id] for query_id in query_ids}
    queries = {query_id: queries[query_id] for query_id in query_ids}

    settings = get_settings()
    provider = get_embedding_provider()
    fingerprint = embedding_fingerprint(settings.embedding_base_url, provider.name, settings.embedding_model, provider.dimension)
    client, collection_name = prepare_dense_index(args.data_dir, fingerprint, provider.dimension)
    index_corpus(
        client,
        collection_name,
        corpus,
        provider,
        args.embedding_workers,
    )
    query_vectors = embed_query_vectors(
        args.data_dir,
        fingerprint,
        queries,
        provider,
        args.embedding_workers,
    )

    rankings, retrieval_latencies = evaluate_routes(
        client,
        collection_name,
        corpus,
        queries,
        query_vectors,
        route_limit=args.route_limit,
        rrf_k=settings.rrf_k,
        bm25_prefilter_terms=settings.retrieval_bm25_prefilter_terms,
    )
    route_metrics = {
        route: compute_ir_metrics(qrels, route_rankings, CUTOFFS)
        for route, route_rankings in rankings.items()
    }
    report = build_report(
        corpus_count=len(corpus),
        qrels=qrels,
        settings=settings,
        route_limit=args.route_limit,
        route_metrics=route_metrics,
        retrieval_latencies=retrieval_latencies,
        max_queries=args.max_queries,
    )
    write_outputs(report, args.output_json, args.output_md)
    print(json.dumps(report["results"], ensure_ascii=False, indent=2), flush=True)
    print(f"JSON report: {args.output_json}", flush=True)
    print(f"Markdown report: {args.output_md}", flush=True)
    return 0


def ensure_dataset(data_dir: Path) -> Path:
    archive = data_dir / "scifact.zip"
    dataset_root = data_dir / "dataset" / "scifact"
    required = (
        dataset_root / "corpus.jsonl",
        dataset_root / "queries.jsonl",
        dataset_root / "qrels" / "test.tsv",
    )
    if all(path.exists() for path in required):
        return dataset_root

    data_dir.mkdir(parents=True, exist_ok=True)
    if not archive.exists() or file_md5(archive) != DATASET_MD5:
        print(f"Downloading {DATASET_URL}", flush=True)
        urllib.request.urlretrieve(DATASET_URL, archive)
    digest = file_md5(archive)
    if digest != DATASET_MD5:
        raise RuntimeError(f"SciFact archive checksum mismatch: expected {DATASET_MD5}, got {digest}")

    destination = data_dir / "dataset"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        destination_resolved = destination.resolve()
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise RuntimeError(f"Unsafe path in SciFact archive: {member.filename}")
        bundle.extractall(destination)
    if not all(path.exists() for path in required):
        raise RuntimeError("SciFact archive does not contain the expected BEIR files")
    return dataset_root


def load_corpus(path: Path) -> dict[str, CorpusRow]:
    rows: dict[str, CorpusRow] = {}
    for payload in load_jsonl(path):
        document_id = str(payload["_id"])
        title = str(payload.get("title") or "").strip()
        content = str(payload.get("text") or "").strip()
        document = BenchmarkDocument(id=document_id, file_name=f"scifact-{document_id}.json")
        chunk = BenchmarkChunk(
            id=document_id,
            document_id=document_id,
            content=content,
            title_path=title or None,
        )
        rows[document_id] = CorpusRow(
            document=document,
            chunk=chunk,
            embedding_input=embedding_text(document, chunk),
            lexical_input=keyword_search_text(chunk, document),
        )
    return rows


def load_queries(path: Path) -> dict[str, str]:
    return {
        str(payload["_id"]): str(payload["text"]).strip()
        for payload in load_jsonl(path)
    }


def load_qrels(path: Path) -> dict[str, dict[str, float]]:
    qrels: dict[str, dict[str, float]] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        query_id, corpus_id, score = line.split("\t")
        qrels.setdefault(query_id, {})[corpus_id] = float(score)
    return qrels


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def embedding_fingerprint(base_url: str, provider: str, model: str, dimension: int) -> str:
    value = f"v1\n{base_url.rstrip('/')}\n{provider}\n{model}\n{dimension}\n{DATASET_MD5}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def prepare_dense_index(data_dir: Path, fingerprint: str, dimension: int):
    from qdrant_client import QdrantClient, models

    client = QdrantClient(path=str(data_dir / "qdrant"))
    collection_name = f"beir_scifact_{fingerprint}"
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )
    return client, collection_name


def index_corpus(
    client,
    collection_name: str,
    corpus: dict[str, CorpusRow],
    provider,
    workers: int,
) -> None:
    from qdrant_client import models

    existing_ids = load_existing_point_ids(client, collection_name)
    pending = [row for document_id, row in corpus.items() if document_id not in existing_ids]
    if not pending:
        print(f"Dense index ready: {len(corpus)} corpus records", flush=True)
        return

    print(f"Embedding {len(pending)} missing corpus records with {workers} workers", flush=True)
    batches = list(batched(pending, 10))
    completed = len(existing_ids)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(provider.embed_texts, [row.embedding_input for row in batch]): batch
            for batch in batches
        }
        for future in as_completed(futures):
            batch = futures[future]
            vectors = future.result()
            points = [
                models.PointStruct(
                    id=int(row.document.id),
                    vector=vector,
                    payload={"corpus_id": row.document.id},
                )
                for row, vector in zip(batch, vectors, strict=True)
            ]
            client.upsert(collection_name=collection_name, points=points, wait=True)
            completed += len(batch)
            if completed == len(corpus) or completed % 100 <= len(batch):
                elapsed = time.perf_counter() - started
                print(f"Indexed {completed}/{len(corpus)} records ({elapsed:.1f}s)", flush=True)

    indexed_count = int(client.count(collection_name=collection_name, exact=True).count)
    if indexed_count != len(corpus):
        raise RuntimeError(f"Dense index contains {indexed_count} records; expected {len(corpus)}")


def load_existing_point_ids(client, collection_name: str) -> set[str]:
    point_ids: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        point_ids.update(str(point.payload["corpus_id"]) for point in points)
        if offset is None:
            return point_ids


def embed_query_vectors(
    data_dir: Path,
    fingerprint: str,
    queries: dict[str, str],
    provider,
    workers: int,
) -> dict[str, list[float]]:
    cache_path = data_dir / f"query-vectors-{fingerprint}.json"
    cached: dict[str, list[float]] = {}
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    missing_ids = [query_id for query_id in queries if query_id not in cached]
    if missing_ids:
        print(f"Embedding {len(missing_ids)} test queries", flush=True)
        batches = list(batched(missing_ids, 10))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(provider.embed_texts, [queries[query_id] for query_id in batch]): batch
                for batch in batches
            }
            for future in as_completed(futures):
                batch = futures[future]
                vectors = future.result()
                cached.update({query_id: vector for query_id, vector in zip(batch, vectors, strict=True)})
        cache_path.write_text(json.dumps(cached), encoding="utf-8")
    return {query_id: cached[query_id] for query_id in queries}


def evaluate_routes(
    client,
    collection_name: str,
    corpus: dict[str, CorpusRow],
    queries: dict[str, str],
    query_vectors: dict[str, list[float]],
    route_limit: int,
    rrf_k: int,
    bm25_prefilter_terms: int,
) -> tuple[dict[str, dict[str, list[str]]], list[float]]:
    rankings: dict[str, dict[str, list[str]]] = {
        "dense": {},
        "bm25": {},
        "hybrid": {},
    }
    latencies: list[float] = []
    corpus_values = list(corpus.values())
    for index, (query_id, query) in enumerate(queries.items(), start=1):
        started = time.perf_counter()
        dense_candidates = dense_route(
            client,
            collection_name,
            corpus,
            query,
            query_vectors[query_id],
            route_limit,
        )
        bm25_candidates = bm25_route(
            corpus_values,
            query,
            route_limit,
            bm25_prefilter_terms,
        )
        route_candidates = {"dense": dense_candidates, "bm25": bm25_candidates}
        fused = fuse_candidates(route_candidates, rrf_k)
        latencies.append((time.perf_counter() - started) * 1000)

        rankings["dense"][query_id] = [candidate.chunk.document_id for candidate in dense_candidates]
        rankings["bm25"][query_id] = [candidate.chunk.document_id for candidate in bm25_candidates]
        rankings["hybrid"][query_id] = [candidate.chunk.document_id for candidate in fused[:route_limit]]
        if index % 25 == 0 or index == len(queries):
            print(f"Evaluated {index}/{len(queries)} queries", flush=True)
    return rankings, latencies


def dense_route(
    client,
    collection_name: str,
    corpus: dict[str, CorpusRow],
    query: str,
    query_vector: list[float],
    route_limit: int,
) -> list[RetrievalCandidate]:
    result = client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=route_limit,
        with_payload=True,
        with_vectors=False,
    )
    candidates: list[RetrievalCandidate] = []
    for rank, point in enumerate(result.points, start=1):
        document_id = str(point.payload["corpus_id"])
        row = corpus[document_id]
        chunk = retrieved_chunk(row, float(point.score or 0), "dense")
        candidates.append(
            RetrievalCandidate(
                chunk=chunk,
                route="dense",
                query=query,
                rank=rank,
                score=chunk.score,
                matched_terms=matched_terms(query, row.lexical_input),
            )
        )
    return candidates


def bm25_route(
    corpus: list[CorpusRow],
    query: str,
    route_limit: int,
    prefilter_terms: int,
) -> list[RetrievalCandidate]:
    query_terms = bm25_query_terms(query)
    filters = query_terms[:prefilter_terms]
    if not filters:
        return []
    candidate_rows = [
        (row.chunk, row.document)
        for row in corpus
        if any(term in row.lexical_input.lower() for term in filters)
    ]
    ranked = rank_bm25_rows(query_terms, candidate_rows)[:route_limit]
    return [
        RetrievalCandidate(
            chunk=retrieved_chunk_from_models(chunk, document, score, "bm25"),
            route="bm25",
            query=query,
            rank=rank,
            score=score,
            matched_terms=matched_terms(query, keyword_search_text(chunk, document)),
        )
        for rank, (chunk, document, score) in enumerate(ranked, start=1)
    ]


def retrieved_chunk(row: CorpusRow, score: float, route: str) -> RetrievedChunk:
    return retrieved_chunk_from_models(row.chunk, row.document, score, route)


def retrieved_chunk_from_models(
    chunk: BenchmarkChunk,
    document: BenchmarkDocument,
    score: float,
    route: str,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        knowledge_base_id="beir-scifact",
        chunk_index=0,
        content=chunk.content,
        score=float(score),
        file_name=document.file_name,
        title_path=chunk.title_path,
        page_number=None,
        section_name=chunk.section_name,
        metadata={"dataset": "BEIR/SciFact"},
        security_level=1,
        retrieval_routes=[route],
    )


def build_report(
    corpus_count: int,
    qrels: dict[str, dict[str, float]],
    settings,
    route_limit: int,
    route_metrics: dict[str, dict[str, float | int]],
    retrieval_latencies: list[float],
    max_queries: int,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        "dataset": {
            "name": "BEIR/SciFact",
            "source": DATASET_URL,
            "homepage": DATASET_HOMEPAGE,
            "beir_homepage": BEIR_HOMEPAGE,
            "beir_paper": BEIR_PAPER,
            "scifact_paper": SCIFACT_PAPER,
            "archive_md5": DATASET_MD5,
            "corpus_count": corpus_count,
            "query_count": len(qrels),
            "relevant_pairs": sum(len(values) for values in qrels.values()),
            "full_test_split": max_queries <= 0,
            "licenses": {
                "claims_and_evidence": "CC BY 4.0",
                "abstract_corpus": "ODC-By 1.0 (via S2ORC)",
            },
        },
        "configuration": {
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": settings.embedding_dimension,
            "retrieval_unit": "one SciFact abstract per BEIR corpus record",
            "dense_engine": "Qdrant local mode / cosine similarity",
            "bm25_implementation": "project rank_bm25_rows / rank-bm25",
            "route_limit": route_limit,
            "rrf_k": settings.rrf_k,
            "bm25_prefilter_terms": settings.retrieval_bm25_prefilter_terms,
            "max_matched_terms": settings.retrieval_max_matched_terms,
            "python": platform.python_version(),
        },
        "results": route_metrics,
        "latency": {
            "scope": "local Qdrant + BM25 + RRF; excludes remote query embedding",
            "queries": len(retrieval_latencies),
            "mean_ms": round(statistics.fmean(retrieval_latencies), 3),
            "p50_ms": round(percentile(retrieval_latencies, 50), 3),
            "p95_ms": round(percentile(retrieval_latencies, 95), 3),
        },
        "limitations": [
            "This is an offline retrieval benchmark, not an end-to-end answer or Agent benchmark.",
            "SciFact relevance labels are document-level; each abstract is therefore kept as one retrieval unit.",
            "Local retrieval latency excludes the remote embedding request and must not be presented as API latency.",
            "The benchmark does not exercise authorization filters, PostgreSQL hydration, SSE, citations, or memory.",
        ],
    }


def write_outputs(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    dataset = report["dataset"]
    config = report["configuration"]
    latency = report["latency"]
    results = report["results"]
    dense = results["dense"]
    hybrid = results["hybrid"]
    recall_delta = (float(hybrid["Recall@10"]) - float(dense["Recall@10"])) * 100
    ndcg_delta = (float(hybrid["nDCG@10"]) - float(dense["nDCG@10"])) * 100
    mrr_delta = (float(hybrid["MRR@10"]) - float(dense["MRR@10"])) * 100
    best_route = max(results, key=lambda route: float(results[route]["nDCG@10"]))
    best_metrics = results[best_route]
    route_labels = {
        "dense": "Dense",
        "bm25": "BM25",
        "hybrid": "Dense + BM25 + RRF",
    }
    if ndcg_delta > 0:
        comparison_summary = (
            "修复后的 BM25 已能为 Dense 提供有效增量，当前无权重 RRF 在锁定配置下改善了排序。"
        )
        resume_result = (
            f"混合检索取得 nDCG@10 {percent(hybrid['nDCG@10'])}、"
            f"Recall@10 {percent(hybrid['Recall@10'])}、MRR@10 {percent(hybrid['MRR@10'])}，"
            f"nDCG@10 相比 Dense 提升 {ndcg_delta:.2f} 个百分点"
        )
        interpretation = (
            "当前 BM25 已能为 Dense 提供有效增量，无权重 RRF 在锁定配置下超过 Dense-only。"
            "train validation 中比较候选深度和 BM25 权重后，现有等权 Top-10 配置仍是本轮最佳；"
            "后续不能针对 test 数字继续调参。"
        )
        next_steps = (
            "1. 保留 BM25 全文词项与日志 matched terms 展示上限的隔离，并用回归测试防止退化。\n"
            "2. 增加其他公开数据集，验证当前等权融合是否能跨领域泛化。\n"
            "3. 只在独立 validation 上评估 cross-encoder reranker，并单独报告效果提升和延迟成本。"
        )
    else:
        comparison_summary = "加入较弱的 BM25 路线并不必然改善排序。"
        resume_result = (
            f"{route_labels[best_route]} 取得 nDCG@10 {percent(best_metrics['nDCG@10'])}、"
            f"Recall@10 {percent(best_metrics['Recall@10'])}、MRR@10 {percent(best_metrics['MRR@10'])}，"
            "并通过基准识别无权重 RRF 的排序退化问题"
        )
        interpretation = (
            "当前 BM25 和无权重 Hybrid 均未超过 Dense。它们不是可以从简历中隐藏的“失败路线”，"
            "而是一次有效的消融实验：公开基准证明当前融合策略需要继续校准。"
        )
        next_steps = (
            "1. 取消 BM25 文档词项的过早截断，同时保留日志 matched terms 的展示上限。\n"
            "2. 在 validation split 上比较 weighted RRF 与原始无权重 RRF。\n"
            "3. 引入 cross-encoder reranker，并单独报告效果提升和延迟成本。"
        )
    rows = []
    for route, label in route_labels.items():
        metrics = results[route]
        rows.append(
            f"| {label} | {percent(metrics['nDCG@10'])} | {percent(metrics['Recall@10'])} | "
            f"{percent(metrics['MRR@10'])} | {percent(metrics['MAP@10'])} | {percent(metrics['Precision@10'])} |"
        )

    return f"""# BEIR/SciFact 公开基准报告

> 生成时间：{report['generated_at']}  
> Git commit：`{report['git_commit']}`  
> 原始机器可读结果：`.run/benchmarks/beir-scifact-results.json`（本地生成，不提交凭据或向量）

## 结论

本项目在 BEIR/SciFact 公开测试集上，以 `{config['embedding_model']}`（{config['embedding_dimension']} 维）完成 Dense、BM25 和无权重 RRF 混合检索的消融评估。测试包含 {dataset['corpus_count']:,} 篇公开科学摘要、{dataset['query_count']} 条 test query 和 {dataset['relevant_pairs']} 个相关性标注。

| 检索方式 | nDCG@10 | Recall@10 | MRR@10 | MAP@10 | Precision@10 |
|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

本次最佳路线是 **{route_labels[best_route]}**。当前无权重 Hybrid 相比 Dense-only 的 nDCG@10、Recall@10、MRR@10 分别变化 {ndcg_delta:+.2f}、{recall_delta:+.2f}、{mrr_delta:+.2f} 个百分点。{comparison_summary}所有路线均使用同一语料、查询、评估代码和 Top-{config['route_limit']} 候选上限。

## 可用于简历的表述

> 基于 FastAPI、Qdrant 与 LangChain 构建 Agentic RAG 系统，在 BEIR/SciFact（{dataset['corpus_count']:,} 篇语料、{dataset['query_count']} 条公开测试查询）上完成 Dense、BM25、RRF 三条检索路线的可复现消融评估；{resume_result}。

这段表述只能用于“检索质量”，不能写成答案准确率或端到端 Agent 准确率。

## 结果解读与下一步

{interpretation}

后续优化应只使用 SciFact train qrels 或另建 validation split 调整 BM25、路线权重和 reranker；test split 应保留为最终验证，避免直接针对本报告的 test 数字调参。建议依次验证：

{next_steps}

## 数据集与许可证

- BEIR 数据页：{dataset['beir_homepage']}
- BEIR 论文：{dataset['beir_paper']}
- SciFact 项目主页：{dataset['homepage']}
- SciFact 论文：{dataset['scifact_paper']}
- 固定下载地址：{dataset['source']}
- 文件校验：MD5 `{dataset['archive_md5']}`（沿用 BEIR 发布页校验值）
- Claims/evidence 标注：{dataset['licenses']['claims_and_evidence']}
- Abstract corpus：{dataset['licenses']['abstract_corpus']}
- 本次运行：{'完整 test split' if dataset['full_test_split'] else '子集运行，不可当作完整基准'}

## 配置与方法

| 配置 | 值 |
|---|---|
| Embedding | `{config['embedding_model']}`, {config['embedding_dimension']} 维 |
| 检索单元 | {config['retrieval_unit']} |
| Dense | {config['dense_engine']} |
| BM25 | {config['bm25_implementation']} |
| 路线候选上限 | {config['route_limit']} |
| RRF k | {config['rrf_k']} |
| BM25 预过滤 query terms | {config['bm25_prefilter_terms']} |
| 最大 matched terms | {config['max_matched_terms']} |
| Python | {config['python']} |

标准指标按 query 宏平均：Recall@K、Precision@K、MAP@K、MRR@K 与 graded nDCG@K。Dense、BM25 和 Hybrid 使用完全相同的 qrels。

本地检索计算耗时（不含远程 query embedding）：平均 {latency['mean_ms']} ms，P50 {latency['p50_ms']} ms，P95 {latency['p95_ms']} ms，共 {latency['queries']} 条查询。该数值只用于本机算法分析，不能表述为线上 API 延迟。

## 复现

从项目根目录运行：

```bash
python scripts/benchmark_beir_scifact.py --embedding-workers 8
```

脚本会下载并校验公开数据，将数据、向量索引和 JSON 结果保存到 `.run/benchmarks/`，只把 Markdown 报告写入 `docs/`。

## 适用边界

本报告是检索层公开基准，不包含答案生成、工具选择、Memory、权限过滤、PostgreSQL hydration、引用正确性或 SSE。因此不能用这些数字宣称“Agent 回答准确率”。端到端能力应使用另一套 Agent 行为和回答忠实度评估集。
"""


def percentile(values: list[float], value: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * value / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def percent(value: float | int) -> str:
    return f"{float(value) * 100:.2f}%"


def git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() or "unknown"


def file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - BEIR publishes an MD5 integrity value.
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def batched(values: list[Any], batch_size: int) -> list[list[Any]]:
    return [values[index : index + batch_size] for index in range(0, len(values), batch_size)]


if __name__ == "__main__":
    raise SystemExit(main())
