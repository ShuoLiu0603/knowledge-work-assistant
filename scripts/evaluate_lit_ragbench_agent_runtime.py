from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "apps" / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.agents.runtime import run_agent_turn
from app.agents.state import AgentRunState
from app.core.config import get_settings
from app.db.models.audit_log import AuditLog
from app.db.models.document import Document, DocumentChunk
from app.db.models.knowledge_base import KnowledgeBase, KnowledgeBaseMember
from app.db.models.llm_call_log import LlmCallLog
from app.db.models.user import User
from app.db.session import SessionLocal
from app.evaluation.p0_metrics import citation_scores
from app.llm.provider import get_llm_provider
from app.rag.embeddings import get_embedding_provider
from scripts.evaluate_lit_ragbench_reader import (
    DEFAULT_DATA_DIR,
    LICENSE,
    ensure_data,
    judge_answer,
    load_cases,
    mean,
)


DEFAULT_CACHE = ROOT / ".run" / "p0" / "lit_ragbench_agent_embedding_cache.jsonl"
DEFAULT_OUTPUT = ROOT / ".run" / "p0" / "lit_ragbench_agent_runtime_results.jsonl"
DEFAULT_SUMMARY = ROOT / ".run" / "p0" / "lit_ragbench_agent_runtime_summary.json"
EVALUATION_VERSION = "lit-ragbench-agent-runtime-v1"
EVALUATION_NAMESPACE = uuid.UUID("fd9858f7-9842-402a-b7c5-5948e27d0789")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run LIT-RAGBench through the real production knowledge-base Agent and RAG stack."
    )
    parser.add_argument("--limit", type=int, default=0, help="Run the first N cases; zero runs all cases.")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--embedding-workers", type=int, default=6)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--allow-database-seed", action="store_true")
    args = parser.parse_args()
    if not args.allow_database_seed:
        parser.error("--allow-database-seed is required")
    validate_evaluation_targets()

    ensure_data(args.data_dir)
    cases = load_cases(args.data_dir)
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in selected]
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        parser.error("No LIT-RAGBench cases were selected")

    attach_storage(cases)
    vectors = prepare_embeddings(cases, args.cache, args.embedding_workers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        seed_cases(cases, vectors)
        results = run_cases(cases, args.workers)
        args.output.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results),
            encoding="utf-8",
        )
        summary = summarize(results)
        args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=True, indent=2))
    finally:
        cleanup_cases(cases)
    return 0


def validate_evaluation_targets() -> None:
    settings = get_settings()
    database_name = (make_url(settings.database_url).database or "").casefold()
    if "eval" not in database_name and not settings.database_url.startswith("sqlite"):
        raise RuntimeError("The evaluation database name must contain 'eval'.")


def stable_uuid(value: str) -> str:
    return str(uuid.uuid5(EVALUATION_NAMESPACE, value))


def attach_storage(cases: list[dict]) -> None:
    for case in cases:
        case_id = case["case_id"]
        case["user_id"] = stable_uuid(f"user:{case_id}")
        case["knowledge_base_id"] = stable_uuid(f"kb:{case_id}")
        for index, document in enumerate(case["documents"], start=1):
            document["document_id"] = stable_uuid(f"document:{case_id}:{index}")
            document["chunk_id"] = stable_uuid(f"chunk:{case_id}:{index}")
            document["file_name"] = f"lit-{index}.md"
            document["embedding_hash"] = hashlib.sha256(
                embedding_source(document).encode("utf-8")
            ).hexdigest()


def embedding_source(document: dict) -> str:
    return f"file_name: {document.get('file_name', '')}\ncontent: {document['text']}"


def prepare_embeddings(
    cases: list[dict],
    cache_path: Path,
    workers: int,
) -> dict[str, list[float]]:
    text_by_hash = {
        document["embedding_hash"]: embedding_source(document)
        for case in cases
        for document in case["documents"]
    }
    cached = load_embedding_cache(cache_path)
    missing = [(key, text) for key, text in text_by_hash.items() if key not in cached]
    if not missing:
        return cached
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    batches = [missing[index : index + 10] for index in range(0, len(missing), 10)]
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        provider = get_embedding_provider()
        futures = {
            pool.submit(provider.embed_texts, [text for _, text in batch]): batch
            for batch in batches
        }
        with cache_path.open("a", encoding="utf-8") as handle:
            for future in as_completed(futures):
                batch = futures[future]
                vectors = future.result()
                for (key, _text), vector in zip(batch, vectors, strict=True):
                    cached[key] = vector
                    handle.write(json.dumps({"hash": key, "vector": vector}) + "\n")
                handle.flush()
                completed += len(batch)
                print(f"LIT-RAGBench embeddings: {completed}/{len(missing)}", flush=True)
    return cached


def load_embedding_cache(path: Path) -> dict[str, list[float]]:
    if not path.exists():
        return {}
    return {
        row["hash"]: row["vector"]
        for row in (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def seed_cases(cases: list[dict], vectors: dict[str, list[float]]) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        cleanup_cases(cases, db=db)
        for case in cases:
            db.add(
                User(
                    id=case["user_id"],
                    email=f"lit+{hashlib.sha256(case['case_id'].encode()).hexdigest()[:20]}@example.invalid",
                    username="LIT-RAGBench user",
                    hashed_password="benchmark-not-a-login",
                    is_active=False,
                )
            )
            db.add(
                KnowledgeBase(
                    id=case["knowledge_base_id"],
                    owner_id=case["user_id"],
                    name=f"LIT-RAGBench {case['case_id']}",
                    description="Isolated LIT-RAGBench knowledge base",
                    visibility="private",
                )
            )
            db.add(
                KnowledgeBaseMember(
                    knowledge_base_id=case["knowledge_base_id"],
                    user_id=case["user_id"],
                    role="owner",
                )
            )
            for index, item in enumerate(case["documents"], start=1):
                vector = vectors[item["embedding_hash"]]
                db.add(
                    Document(
                        id=item["document_id"],
                        knowledge_base_id=case["knowledge_base_id"],
                        uploader_id=case["user_id"],
                        file_name=item["file_name"],
                        file_ext=".md",
                        mime_type="text/markdown",
                        file_size=len(item["text"].encode("utf-8")),
                        object_key=f"lit-ragbench-eval/{case['case_id']}/{index}.md",
                        content_hash=hashlib.sha256(item["text"].encode("utf-8")).hexdigest(),
                        status="indexed",
                        chunk_count=1,
                        security_level=1,
                        extra_metadata={"benchmark": "LIT-RAGBench", "qa_types": case["qa_types"]},
                    )
                )
                db.add(
                    DocumentChunk(
                        id=item["chunk_id"],
                        document_id=item["document_id"],
                        knowledge_base_id=case["knowledge_base_id"],
                        chunk_index=0,
                        content=item["text"],
                        token_count=0,
                        embedding=vector,
                        embedding_model=settings.embedding_model,
                        embedding_dimension=len(vector),
                        security_level=1,
                        extra_metadata={
                            "benchmark": "LIT-RAGBench",
                            "lit_positive": bool(item["positive"]),
                            "lit_positive_index": item["positive_index"],
                            "qa_types": case["qa_types"],
                        },
                    )
                )
        db.commit()
    print(f"LIT-RAGBench seed complete: {len(cases)} cases", flush=True)


def cleanup_cases(cases: list[dict], db=None) -> None:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        user_ids = [case["user_id"] for case in cases]
        if not user_ids:
            return
        db.execute(delete(AuditLog).where(AuditLog.actor_user_id.in_(user_ids)))
        db.execute(delete(LlmCallLog).where(LlmCallLog.user_id.in_(user_ids)))
        db.execute(delete(User).where(User.id.in_(user_ids)))
        db.commit()
    finally:
        if owns_session:
            db.close()


def run_cases(cases: list[dict], workers: int) -> list[dict]:
    completed = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_case, case): case["case_id"] for case in cases}
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            completed[result["case_id"]] = result
            print(
                f"LIT-RAGBench Agent: {index}/{len(cases)} "
                f"(rag={result['rag_calls']}, correct={result['correct']})",
                flush=True,
            )
    return [completed[case["case_id"]] for case in cases]


def run_case(case: dict) -> dict:
    state = AgentRunState(
        user_id=case["user_id"],
        knowledge_base_id=case["knowledge_base_id"],
        input=case["query"],
        search_scope="single",
        memory_enabled=False,
        defer_memory_update=True,
    )
    started = time.perf_counter()
    with SessionLocal() as db:
        run_agent_turn(db, state)
        total_tokens = sum(
            db.scalars(select(LlmCallLog.total_tokens).where(LlmCallLog.id.in_(state.llm_log_ids))).all()
        )

    judgment = {"score": 0, "evaluation_reason": ""}
    judge_error = None
    try:
        judgment = judge_answer(get_llm_provider(), case, state.answer)
    except Exception as exc:
        judge_error = str(exc)

    positive_indices = {
        index
        for index, chunk in enumerate(state.rag_chunks, start=1)
        if chunk.metadata.get("lit_positive")
    }
    positive_groups = [{index} for index in sorted(positive_indices)]
    citations = citation_scores(
        state.answer,
        positive_indices,
        len(state.rag_chunks),
        positive_groups=positive_groups,
    )
    retrieved_positive_ids = {
        int(chunk.metadata["lit_positive_index"])
        for chunk in state.rag_chunks
        if chunk.metadata.get("lit_positive")
        and chunk.metadata.get("lit_positive_index") is not None
    }
    expected_positive_count = len(case["positive_context"])
    retrieval_recall = (
        len(retrieved_positive_ids) / expected_positive_count if expected_positive_count else 0.0
    )
    return {
        "evaluation_version": EVALUATION_VERSION,
        "case_id": case["case_id"],
        "source_index": case["source_index"],
        "qa_types": case["qa_types"],
        "family": case["family"],
        "question": case["query"],
        "reference_answer": case["answer"],
        "prediction": state.answer,
        "correct": int(judgment.get("score", 0)) == 1,
        "evaluation_reason": str(judgment.get("evaluation_reason", "")),
        "status": state.status,
        "error": state.error_message,
        "judge_error": judge_error,
        "model_calls": state.model_call_count,
        "tool_calls": state.tool_call_count,
        "rag_calls": state.rag_tool_call_count,
        "rag_queries": state.rag_queries,
        "retrieved_chunk_count": len(state.rag_chunks),
        "retrieved_positive_count": len(retrieved_positive_ids),
        "expected_positive_count": expected_positive_count,
        "retrieval_positive_recall": round(retrieval_recall, 6),
        "retrieval_positive_any": bool(retrieved_positive_ids),
        "retrieval_positive_all": (
            len(retrieved_positive_ids) == expected_positive_count
            if expected_positive_count
            else False
        ),
        **citations,
        "total_tokens": int(total_tokens or 0),
        "latency_seconds": round(time.perf_counter() - started, 3),
        "model": get_settings().llm_model,
        "trace": state.trace,
    }


def summarize(results: list[dict]) -> dict:
    answerable = [row for row in results if row["family"] != "abstention"]
    abstention = [row for row in results if row["family"] == "abstention"]
    retrieved_answerable = [row for row in answerable if row["rag_calls"] > 0]
    direct_answerable = [row for row in answerable if row["rag_calls"] == 0]
    families = sorted({row["family"] for row in results})
    tags = sorted({tag for row in results for tag in row["qa_types"]})
    return {
        "benchmark": "LIT-RAGBench English through production Agent runtime",
        "evaluation_version": EVALUATION_VERSION,
        "license": LICENSE,
        "model": results[0]["model"] if results else "unknown",
        "total": len(results),
        "accuracy": mean(row["correct"] for row in results),
        "answerable_accuracy": mean(row["correct"] for row in answerable),
        "abstention_accuracy": mean(row["correct"] for row in abstention),
        "rag_call_rate": mean(row["rag_calls"] > 0 for row in results),
        "answerable_rag_call_rate": mean(row["rag_calls"] > 0 for row in answerable),
        "abstention_rag_call_rate": mean(row["rag_calls"] > 0 for row in abstention),
        "multiple_rag_call_rate": mean(row["rag_calls"] > 1 for row in results),
        "direct_answer_rate": mean(row["rag_calls"] == 0 for row in results),
        "accuracy_when_rag_called": mean(row["correct"] for row in results if row["rag_calls"] > 0),
        "accuracy_when_direct": mean(row["correct"] for row in results if row["rag_calls"] == 0),
        "answerable_accuracy_when_rag_called": mean(row["correct"] for row in retrieved_answerable),
        "answerable_accuracy_when_direct": mean(row["correct"] for row in direct_answerable),
        "retrieval_positive_any": mean(row["retrieval_positive_any"] for row in answerable),
        "retrieval_positive_all": mean(row["retrieval_positive_all"] for row in answerable),
        "average_positive_recall": mean(row["retrieval_positive_recall"] for row in answerable),
        "citation_precision": mean(
            row["citation_precision"] for row in answerable if row["citation_count"]
        ),
        "citation_coverage": mean(row["citation_coverage"] for row in answerable),
        "invalid_citation_rate": mean(row["invalid_citation_count"] > 0 for row in answerable),
        "average_model_calls": mean(row["model_calls"] for row in results),
        "average_tool_calls": mean(row["tool_calls"] for row in results),
        "average_rag_calls": mean(row["rag_calls"] for row in results),
        "average_total_tokens": mean(row["total_tokens"] for row in results),
        "average_latency_seconds": mean(row["latency_seconds"] for row in results),
        "failed_run_count": sum(row["status"] != "completed" for row in results),
        "judge_error_count": sum(bool(row["judge_error"]) for row in results),
        "by_family": {
            family: summarize_group([row for row in results if row["family"] == family])
            for family in families
        },
        "by_tag": {
            tag: summarize_group([row for row in results if tag in row["qa_types"]])
            for tag in tags
        },
        "failures": [row["case_id"] for row in results if not row["correct"]],
        "limitations": [
            "This is a production-Agent adaptation, not the official supplied-context leaderboard protocol.",
            "Each released chunk is stored as one indexed document chunk in an isolated private knowledge base.",
            "DeepSeek answers and judges using the official English judge prompt; the paper used GPT-4.1 models.",
            "Some English examples retain non-English source chunks from the released dataset.",
        ],
    }


def summarize_group(rows: list[dict]) -> dict:
    return {
        "total": len(rows),
        "accuracy": mean(row["correct"] for row in rows),
        "rag_call_rate": mean(row["rag_calls"] > 0 for row in rows),
        "average_rag_calls": mean(row["rag_calls"] for row in rows),
    }


if __name__ == "__main__":
    raise SystemExit(main())
