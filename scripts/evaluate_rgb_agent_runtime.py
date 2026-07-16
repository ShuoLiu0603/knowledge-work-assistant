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
from app.evaluation.p0_metrics import answer_groups_match, citation_scores, official_rgb_answer_match
from app.llm.provider import get_llm_provider
from app.rag.embeddings import get_embedding_provider
from app.rag.vector_store import (
    chunk_payload,
    embedding_text,
    ensure_qdrant_collection,
    get_qdrant_client,
    qdrant_models,
)
from scripts.evaluate_rgb_reader import (
    DEFAULT_DATA_DIR,
    TASK_FILES,
    build_cases,
    ensure_data,
    judge_factual_error_detection,
    judge_rejection,
    mean,
)

DEFAULT_CACHE = ROOT / ".run" / "p0" / "rgb_agent_embedding_cache.jsonl"
DEFAULT_OUTPUT = ROOT / ".run" / "p0" / "rgb_agent_runtime_results.jsonl"
DEFAULT_SUMMARY = ROOT / ".run" / "p0" / "rgb_agent_runtime_summary.json"
EVALUATION_VERSION = "rgb-derived-agent-runtime-v1"
EVALUATION_NAMESPACE = uuid.UUID("15581a13-2c8d-41be-8946-952afcbe357e")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run RGB-derived cases through the production knowledge-base Agent and RAG stack."
    )
    parser.add_argument("--per-task", type=int, default=25)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--embedding-workers", type=int, default=6)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--allow-database-seed", action="store_true")
    args = parser.parse_args()
    if not args.allow_database_seed:
        parser.error("--allow-database-seed is required")
    validate_evaluation_targets()

    ensure_data(args.data_dir)
    cases = build_cases(args.data_dir, args.per_task)
    if args.case_id:
        requested = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in requested]
    if not cases:
        parser.error("No RGB cases were selected")
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
        cleanup_qdrant_collection()
    return 0


def validate_evaluation_targets() -> None:
    settings = get_settings()
    database_name = (make_url(settings.database_url).database or "").casefold()
    if "eval" not in database_name and not settings.database_url.startswith("sqlite"):
        raise RuntimeError("The evaluation database name must contain 'eval'.")
    if "eval" not in settings.qdrant_collection.casefold():
        raise RuntimeError("The evaluation Qdrant collection name must contain 'eval'.")


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
            document["point_id"] = stable_uuid(f"point:{case_id}:{index}")
            document["file_name"] = f"rgb-{index}.md"
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
                batch_vectors = future.result()
                for (key, _text), vector in zip(batch, batch_vectors, strict=True):
                    cached[key] = vector
                    handle.write(json.dumps({"hash": key, "vector": vector}) + "\n")
                handle.flush()
                completed += len(batch)
                print(f"RGB Agent embeddings: {completed}/{len(missing)}", flush=True)
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
    reset_qdrant_collection()
    with SessionLocal() as db:
        cleanup_cases(cases, db=db)
        for case in cases:
            db.add(
                User(
                    id=case["user_id"],
                    email=f"rgb+{hashlib.sha256(case['case_id'].encode()).hexdigest()[:20]}@example.invalid",
                    username="RGB benchmark user",
                    hashed_password="benchmark-not-a-login",
                    is_active=False,
                )
            )
            db.add(
                KnowledgeBase(
                    id=case["knowledge_base_id"],
                    owner_id=case["user_id"],
                    name=f"RGB {case['case_id']}",
                    description="Isolated RGB-derived benchmark knowledge base",
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
                db.add(
                    Document(
                        id=item["document_id"],
                        knowledge_base_id=case["knowledge_base_id"],
                        uploader_id=case["user_id"],
                        file_name=item["file_name"],
                        file_ext=".md",
                        mime_type="text/markdown",
                        file_size=len(item["text"].encode("utf-8")),
                        object_key=f"rgb-eval/{case['case_id']}/{index}.md",
                        content_hash=hashlib.sha256(item["text"].encode("utf-8")).hexdigest(),
                        status="indexed",
                        chunk_count=1,
                        security_level=1,
                        extra_metadata={"benchmark": "RGB-derived"},
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
                        qdrant_point_id=item["point_id"],
                        security_level=1,
                        extra_metadata={
                            "rgb_index": index,
                            "positive": bool(item.get("positive")),
                            "positive_group": item.get("positive_group"),
                            "counterfactual": bool(item.get("counterfactual")),
                        },
                    )
                )
        db.commit()
        seed_qdrant_points(db, cases, vectors)
    print(f"RGB Agent seed complete: {len(cases)} cases", flush=True)


def seed_qdrant_points(db, cases: list[dict], vectors: dict[str, list[float]]) -> None:
    client = get_qdrant_client()
    models = qdrant_models()
    points = []
    for case in cases:
        for item in case["documents"]:
            document = db.get(Document, item["document_id"])
            chunk = db.get(DocumentChunk, item["chunk_id"])
            source = embedding_text(document, chunk)
            if source != embedding_source(item):
                raise RuntimeError("RGB embedding source does not match production ingestion format")
            points.append(
                models.PointStruct(
                    id=item["point_id"],
                    vector=vectors[item["embedding_hash"]],
                    payload=chunk_payload(document, chunk),
                )
            )
    for index in range(0, len(points), 100):
        client.upsert(
            collection_name=get_settings().qdrant_collection,
            points=points[index : index + 100],
            wait=True,
        )


def reset_qdrant_collection() -> None:
    cleanup_qdrant_collection()
    ensure_qdrant_collection()


def cleanup_qdrant_collection() -> None:
    collection_name = get_settings().qdrant_collection
    if "eval" not in collection_name.casefold():
        return
    client = get_qdrant_client()
    if client.collection_exists(collection_name):
        client.delete_collection(collection_name=collection_name)


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
                f"RGB Agent cases: {index}/{len(cases)} "
                f"(tools={result['tool_calls']}, success={result['success']})",
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
            db.scalars(
                select(LlmCallLog.total_tokens).where(LlmCallLog.id.in_(state.llm_log_ids))
            ).all()
        )

    rejection_judgment = ""
    factual_error_judgment = ""
    judge_error = None
    try:
        provider = get_llm_provider()
        if case["task"] == "negative_rejection":
            rejection_judgment = judge_rejection(provider, case["query"], state.answer)
        elif case["task"] == "counterfactual_robustness":
            factual_error_judgment = judge_factual_error_detection(provider, state.answer)
    except Exception as exc:
        judge_error = str(exc)

    positive_indices = {
        index
        for index, chunk in enumerate(state.rag_chunks, start=1)
        if chunk.metadata.get("positive")
    }
    positive_groups: dict[int, set[int]] = {}
    for index, chunk in enumerate(state.rag_chunks, start=1):
        group = chunk.metadata.get("positive_group")
        if group is not None:
            positive_groups.setdefault(int(group), set()).add(index)
    citations = citation_scores(
        state.answer,
        positive_indices,
        len(state.rag_chunks),
        positive_groups=list(positive_groups.values()),
    )
    official_correct = official_rgb_answer_match(state.answer, case["answer"])
    normalized_correct = answer_groups_match(state.answer, case["answer"])
    rejection_detected = rejection_judgment == "NOT_ADDRESSED"
    factual_error_detected = factual_error_judgment == "IDENTIFIED"
    success = {
        "noise_robustness": official_correct,
        "negative_rejection": rejection_detected,
        "information_integration": official_correct,
        "counterfactual_robustness": factual_error_detected and official_correct,
    }[case["task"]]
    expected_positive_count = sum(bool(item.get("positive")) for item in case["documents"])
    return {
        "evaluation_version": EVALUATION_VERSION,
        "case_id": case["case_id"],
        "source_id": case["source_id"],
        "task": case["task"],
        "query": case["query"],
        "answer": case["answer"],
        "prediction": state.answer,
        "success": success,
        "official_answer_correct": official_correct,
        "normalized_answer_correct": normalized_correct,
        "rejection_detected": rejection_detected,
        "factual_error_detected": factual_error_detected,
        "rejection_judgment": rejection_judgment,
        "factual_error_judgment": factual_error_judgment,
        "judge_error": judge_error,
        **citations,
        "status": state.status,
        "error": state.error_message,
        "model_calls": state.model_call_count,
        "tool_calls": state.tool_call_count,
        "memory_calls": state.memory_tool_call_count,
        "rag_calls": state.rag_tool_call_count,
        "rag_queries": state.rag_queries,
        "retrieved_chunk_count": len(state.rag_chunks),
        "retrieved_positive_count": len(positive_indices),
        "expected_positive_count": expected_positive_count,
        "retrieval_positive_recall": (
            round(len(positive_indices) / expected_positive_count, 6)
            if expected_positive_count
            else 0.0
        ),
        "total_tokens": int(total_tokens or 0),
        "latency_seconds": round(time.perf_counter() - started, 3),
        "model": get_settings().llm_model,
        "trace": state.trace,
    }


def summarize(results: list[dict]) -> dict:
    tasks = {}
    for task in TASK_FILES:
        rows = [row for row in results if row["task"] == task]
        tasks[task] = {
            "total": len(rows),
            "success_rate": mean(row["success"] for row in rows),
            "official_answer_accuracy": mean(row["official_answer_correct"] for row in rows),
            "rag_call_rate": mean(row["rag_calls"] > 0 for row in rows),
            "average_rag_calls": mean(row["rag_calls"] for row in rows),
        }
    answerable = [row for row in results if row["task"] in {"noise_robustness", "information_integration"}]
    negative = [row for row in results if row["task"] == "negative_rejection"]
    counterfactual = [row for row in results if row["task"] == "counterfactual_robustness"]
    detected = [row for row in counterfactual if row["factual_error_detected"]]
    return {
        "benchmark": "RGB-derived through production knowledge-base Agent runtime",
        "evaluation_version": EVALUATION_VERSION,
        "license": "CC BY-NC-SA 4.0; noncommercial use only",
        "model": results[0]["model"] if results else "unknown",
        "total": len(results),
        "custom_combined_success_rate": mean(row["success"] for row in results),
        "official_answer_match_accuracy": mean(row["official_answer_correct"] for row in answerable),
        "negative_rejection_rate": mean(row["rejection_detected"] for row in negative),
        "factual_error_detection_rate": mean(row["factual_error_detected"] for row in counterfactual),
        "error_correction_rate_given_detection": mean(row["official_answer_correct"] for row in detected),
        "citation_precision": mean(row["citation_precision"] for row in answerable if row["citation_count"]),
        "citation_coverage": mean(row["citation_coverage"] for row in answerable),
        "invalid_citation_rate": mean(row["invalid_citation_count"] > 0 for row in answerable),
        "rag_call_rate": mean(row["rag_calls"] > 0 for row in results),
        "direct_answer_rate": mean(row["tool_calls"] == 0 for row in results),
        "multiple_rag_call_rate": mean(row["rag_calls"] > 1 for row in results),
        "average_model_calls": mean(row["model_calls"] for row in results),
        "average_tool_calls": mean(row["tool_calls"] for row in results),
        "average_total_tokens": mean(row["total_tokens"] for row in results),
        "average_latency_seconds": mean(row["latency_seconds"] for row in results),
        "failed_run_count": sum(row["status"] != "completed" for row in results),
        "tasks": tasks,
        "failures": [row["case_id"] for row in results if not row["success"]],
        "limitations": [
            "Stratified subset, not the full RGB benchmark.",
            "Each RGB passage is stored as one indexed document chunk in an isolated private knowledge base.",
            "Runs the production direct knowledge-base path with memory disabled and RAG available; the model is not forced to call RAG.",
            "DeepSeek is both answer model and official-style judge, so this is not an official leaderboard result.",
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
