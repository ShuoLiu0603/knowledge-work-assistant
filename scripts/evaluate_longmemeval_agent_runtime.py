from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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
from app.db.models.llm_call_log import LlmCallLog
from app.db.models.user import User
from app.db.models.user_memory import UserMemory
from app.db.session import SessionLocal
from app.llm.provider import get_llm_provider
from scripts.evaluate_longmemeval_memory import (
    DEFAULT_CACHE,
    DEFAULT_DATA_DIR,
    QUESTION_TYPES,
    ensure_data,
    ensure_embeddings,
    evaluate_answer,
    load_json,
    mean,
    prepare_case,
    retrieval_all,
    retrieval_any,
    retrieval_recall,
    select_cases,
)

DEFAULT_OUTPUT = ROOT / ".run" / "p0" / "longmemeval_agent_runtime_results.jsonl"
DEFAULT_SUMMARY = ROOT / ".run" / "p0" / "longmemeval_agent_runtime_summary.json"
EVALUATION_VERSION = "longmemeval-agent-runtime-v1"
CURRENT_BUDGET = {"model": 6, "tool": 4, "memory": 2, "rag": 3}
CEILING_BUDGET = {"model": 20, "tool": 20, "memory": 10, "rag": 10}
EVALUATION_NAMESPACE = uuid.UUID("9b3100d3-5db4-4460-aa0c-a5fc535895d7")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run LongMemEval through the real production Agent runtime and memory/RAG tools."
    )
    parser.add_argument("--per-type", type=int, default=4)
    parser.add_argument("--abstention", type=int, default=6)
    parser.add_argument("--question-id", action="append", default=[])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--embedding-workers", type=int, default=6)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--budgets",
        choices=("current", "ceiling", "both"),
        default="both",
        help="Compare current production limits with the highest limits accepted by Settings.",
    )
    parser.add_argument(
        "--allow-database-seed",
        action="store_true",
        help="Required because the benchmark creates temporary users and memory rows.",
    )
    args = parser.parse_args()
    if not args.allow_database_seed:
        parser.error("--allow-database-seed is required")
    validate_evaluation_database()

    ensure_data(args.data_dir)
    rows = load_json(args.data_dir / "longmemeval_s_cleaned.json")
    oracle_by_id = {
        row["question_id"]: row
        for row in load_json(args.data_dir / "longmemeval_oracle.json")
    }
    selected = (
        [row for row in rows if row["question_id"] in set(args.question_id)]
        if args.question_id
        else select_cases(rows, args.per_type, args.abstention)
    )
    if not selected:
        parser.error("No LongMemEval cases were selected")
    cases = [prepare_case(row, oracle_by_id[row["question_id"]]) for row in selected]
    assign_storage_ids(cases)
    embeddings = prepare_embeddings(cases, args.cache, args.embedding_workers)
    budgets = selected_budgets(args.budgets)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    original_budget = read_runtime_budget()
    results: list[dict] = []
    try:
        seed_cases(cases, embeddings)
        args.output.write_text("", encoding="utf-8")
        for budget_name, budget in budgets:
            apply_runtime_budget(budget)
            scenario_results = run_scenario(cases, budget_name, budget, args.workers)
            results.extend(scenario_results)
            with args.output.open("a", encoding="utf-8") as handle:
                for result in scenario_results:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    finally:
        apply_runtime_budget(original_budget)
        cleanup_cases(cases)

    summary = summarize(results, budgets)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


def validate_evaluation_database() -> None:
    settings = get_settings()
    url = make_url(settings.database_url)
    database_name = (url.database or "").casefold()
    if "eval" not in database_name and not settings.database_url.startswith("sqlite"):
        raise RuntimeError(
            "Refusing to seed a non-evaluation database. Use a database whose name contains 'eval'."
        )


def prepare_embeddings(cases: list[dict], cache_path: Path, workers: int) -> dict[str, list[float]]:
    text_by_hash = {
        turn["text_hash"]: turn["embedding_text"]
        for case in cases
        for turn in case["turns"]
    }
    cached = load_required_embeddings(cache_path, set(text_by_hash))
    if all(key in cached for key in text_by_hash):
        return cached
    return ensure_embeddings(text_by_hash, cache_path, workers)


def load_required_embeddings(path: Path, required_hashes: set[str]) -> dict[str, list[float]]:
    if not path.exists() or not required_hashes:
        return {}
    cached = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if len(line) < 75:
                continue
            key = line[10:74]
            if key not in required_hashes:
                continue
            cached[key] = json.loads(line)["vector"]
            if len(cached) == len(required_hashes):
                break
    return cached


def selected_budgets(value: str) -> list[tuple[str, dict[str, int]]]:
    if value == "current":
        return [("current", CURRENT_BUDGET)]
    if value == "ceiling":
        return [("ceiling", CEILING_BUDGET)]
    return [("current", CURRENT_BUDGET), ("ceiling", CEILING_BUDGET)]


def read_runtime_budget() -> dict[str, int]:
    settings = get_settings()
    return {
        "model": settings.agent_max_model_calls,
        "tool": settings.agent_max_tool_calls,
        "memory": settings.agent_max_memory_calls,
        "rag": settings.agent_max_rag_calls,
    }


def apply_runtime_budget(budget: dict[str, int]) -> None:
    settings = get_settings()
    settings.agent_max_model_calls = budget["model"]
    settings.agent_max_tool_calls = budget["tool"]
    settings.agent_max_memory_calls = budget["memory"]
    settings.agent_max_rag_calls = budget["rag"]


def benchmark_user_id(question_id: str) -> str:
    return str(uuid.uuid5(EVALUATION_NAMESPACE, question_id))


def benchmark_memory_id(question_id: str, turn_id: str) -> str:
    return str(uuid.uuid5(EVALUATION_NAMESPACE, f"{question_id}:{turn_id}"))


def assign_storage_ids(cases: list[dict]) -> None:
    for case in cases:
        for index, turn in enumerate(case["turns"]):
            turn["storage_id"] = f"{turn['id']}:{index}"


def seed_cases(cases: list[dict], embeddings: dict[str, list[float]]) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        cleanup_cases(cases, db=db)
        for index, case in enumerate(cases, start=1):
            user_id = benchmark_user_id(case["question_id"])
            db.add(
                User(
                    id=user_id,
                    email=f"longmemeval+{case['question_id']}@example.invalid",
                    username="LongMemEval benchmark user",
                    hashed_password="benchmark-not-a-login",
                    is_active=False,
                )
            )
            db.flush()
            mappings = [
                memory_mapping(case, turn, user_id, embeddings[turn["text_hash"]], settings)
                for turn in case["turns"]
            ]
            db.bulk_insert_mappings(UserMemory, mappings)
            db.commit()
            print(f"Agentic LongMemEval seed: {index}/{len(cases)}", flush=True)


def memory_mapping(
    case: dict,
    turn: dict,
    user_id: str,
    embedding: list[float],
    settings,
) -> dict:
    content = turn["embedding_text"]
    touched_at = parse_longmemeval_date(turn["date"])
    return {
        "id": benchmark_memory_id(case["question_id"], turn["storage_id"]),
        "user_id": user_id,
        "content": content,
        "normalized_content": " ".join(content.casefold().split()),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "status": "active",
        "kind": "fact",
        "category": "general",
        "canonical_key": "",
        "memory_layer": "semantic",
        "profile_slot": "",
        "scope_type": "user",
        "scope_id": "",
        "pinned": False,
        "revision": 1,
        "source_text": content,
        "embedding": embedding,
        "embedding_model": settings.embedding_model,
        "embedding_dimension": len(embedding),
        "merge_count": 0,
        "touched_count": 0,
        "extra_metadata": {
            "benchmark": "LongMemEval-S",
            "question_id": case["question_id"],
            "session_id": turn["session_id"],
            "turn_id": turn["id"],
        },
        "valid_at": touched_at,
        "created_at": touched_at,
        "updated_at": touched_at,
        "last_touched_at": touched_at,
    }


def parse_longmemeval_date(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y/%m/%d (%a) %H:%M")
    return parsed.replace(tzinfo=timezone.utc)


def cleanup_cases(cases: list[dict], db=None) -> None:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        user_ids = [benchmark_user_id(case["question_id"]) for case in cases]
        if not user_ids:
            return
        db.execute(delete(LlmCallLog).where(LlmCallLog.user_id.in_(user_ids)))
        db.execute(delete(UserMemory).where(UserMemory.user_id.in_(user_ids)))
        db.execute(delete(User).where(User.id.in_(user_ids)))
        db.commit()
    finally:
        if owns_session:
            db.close()


def run_scenario(
    cases: list[dict],
    budget_name: str,
    budget: dict[str, int],
    workers: int,
) -> list[dict]:
    completed: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_case, case, budget_name, budget): case["question_id"]
            for case in cases
        }
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            completed[result["question_id"]] = result
            print(
                f"Agentic LongMemEval {budget_name}: {index}/{len(cases)} "
                f"(memory={result['memory_calls']}, rag={result['rag_calls']}, "
                f"correct={result['answer_correct']})",
                flush=True,
            )
    return [completed[case["question_id"]] for case in cases]


def run_case(case: dict, budget_name: str, budget: dict[str, int]) -> dict:
    user_id = benchmark_user_id(case["question_id"])
    state = AgentRunState(
        user_id=user_id,
        knowledge_base_id=None,
        input=f"Question date: {case['question_date']}\nQuestion: {case['question']}",
        search_scope="accessible",
        memory_enabled=True,
        defer_memory_update=True,
    )
    started = time.perf_counter()
    with SessionLocal() as db:
        run_agent_turn(db, state)
        token_usage = db.scalar(
            select(LlmCallLog.total_tokens)
            .where(LlmCallLog.id.in_(state.llm_log_ids))
        ) if len(state.llm_log_ids) == 1 else sum(
            db.scalars(
                select(LlmCallLog.total_tokens).where(LlmCallLog.id.in_(state.llm_log_ids))
            ).all()
        )

    answer_correct = False
    judge_error = None
    try:
        answer_correct, _source = evaluate_answer(get_llm_provider(), case, state.answer)
    except Exception as exc:
        judge_error = str(exc)

    recalled_ids = {str(item.get("id")) for item in state.long_term_memories}
    relevant_ids = {
        benchmark_memory_id(case["question_id"], turn["storage_id"])
        for turn in case["turns"]
        if turn["relevant"]
    }
    memory_observations = [
        item for item in state.tool_observations if item.get("tool") == "memory"
    ]
    rag_observations = [
        item for item in state.tool_observations if item.get("tool") == "rag"
    ]
    return {
        "evaluation_version": EVALUATION_VERSION,
        "budget_name": budget_name,
        "budget": budget,
        "question_id": case["question_id"],
        "question_type": case["question_type"],
        "question": case["question"],
        "reference_answer": case["answer"],
        "abstention": case["abstention"],
        "answer": state.answer,
        "answer_correct": answer_correct,
        "judge_error": judge_error,
        "status": state.status,
        "error": state.error_message,
        "model_calls": state.model_call_count,
        "tool_calls": state.tool_call_count,
        "memory_calls": state.memory_tool_call_count,
        "rag_calls": state.rag_tool_call_count,
        "memory_queries": state.memory_queries,
        "rag_queries": state.rag_queries,
        "memory_query_changed": len(set(query.casefold() for query in state.memory_queries)) > 1,
        "recalled_memory_count": len(recalled_ids),
        "relevant_recalled_count": len(recalled_ids & relevant_ids),
        "relevant_memory_count": len(relevant_ids),
        "memory_recall_any": retrieval_any(recalled_ids, relevant_ids),
        "memory_recall_all": retrieval_all(recalled_ids, relevant_ids),
        "memory_recall": retrieval_recall(recalled_ids, relevant_ids),
        "memory_new_results": sum(item.get("new_result_count", 0) for item in memory_observations),
        "memory_no_progress_calls": sum(item.get("new_result_count", 0) == 0 for item in memory_observations),
        "rag_new_results": sum(item.get("new_result_count", 0) for item in rag_observations),
        "rag_no_progress_calls": sum(item.get("new_result_count", 0) == 0 for item in rag_observations),
        "model_budget_reached": state.model_call_count >= budget["model"],
        "tool_budget_reached": state.tool_call_count >= budget["tool"],
        "memory_budget_reached": state.memory_tool_call_count >= budget["memory"],
        "rag_budget_reached": state.rag_tool_call_count >= budget["rag"],
        "total_tokens": int(token_usage or 0),
        "latency_seconds": round(time.perf_counter() - started, 3),
        "trace": state.trace,
        "tool_observations": state.tool_observations,
        "model": get_settings().llm_model,
    }


def summarize(
    results: list[dict],
    budgets: list[tuple[str, dict[str, int]]],
) -> dict:
    scenarios = {}
    for budget_name, budget in budgets:
        rows = [row for row in results if row["budget_name"] == budget_name]
        scenarios[budget_name] = summarize_scenario(rows, budget)

    comparison = None
    if {"current", "ceiling"}.issubset(scenarios):
        current = {row["question_id"]: row for row in results if row["budget_name"] == "current"}
        ceiling = {row["question_id"]: row for row in results if row["budget_name"] == "ceiling"}
        shared_ids = current.keys() & ceiling.keys()
        comparison = {
            "accuracy_delta": round(
                scenarios["ceiling"]["qa_accuracy"] - scenarios["current"]["qa_accuracy"],
                6,
            ),
            "improved_count": sum(not current[key]["answer_correct"] and ceiling[key]["answer_correct"] for key in shared_ids),
            "regressed_count": sum(current[key]["answer_correct"] and not ceiling[key]["answer_correct"] for key in shared_ids),
            "ceiling_exceeded_current_memory_limit_count": sum(ceiling[key]["memory_calls"] > CURRENT_BUDGET["memory"] for key in shared_ids),
            "ceiling_exceeded_current_tool_limit_count": sum(ceiling[key]["tool_calls"] > CURRENT_BUDGET["tool"] for key in shared_ids),
            "ceiling_exceeded_current_model_limit_count": sum(ceiling[key]["model_calls"] > CURRENT_BUDGET["model"] for key in shared_ids),
            "budget_attributable_improvement_count": sum(
                not current[key]["answer_correct"]
                and ceiling[key]["answer_correct"]
                and (
                    ceiling[key]["memory_calls"] > CURRENT_BUDGET["memory"]
                    or ceiling[key]["tool_calls"] > CURRENT_BUDGET["tool"]
                    or ceiling[key]["model_calls"] > CURRENT_BUDGET["model"]
                )
                for key in shared_ids
            ),
        }

    return {
        "benchmark": "LongMemEval-S cleaned through production Agent runtime",
        "evaluation_version": EVALUATION_VERSION,
        "model": get_settings().llm_model,
        "judge": "DeepSeek with LongMemEval official task-specific judge prompts",
        "database": make_url(get_settings().database_url).get_backend_name(),
        "memory_vector_index_enabled": get_settings().memory_vector_index_enabled,
        "memory_semantic_limit": get_settings().memory_semantic_limit,
        "memory_recall_candidate_limit": get_settings().memory_recall_candidate_limit,
        "production_runtime_unmodified": True,
        "tools_available": ["memory", "rag"],
        "scenarios": scenarios,
        "comparison": comparison,
        "limitations": [
            "The benchmark seeds raw dated conversation turns as semantic memories; it does not evaluate memory extraction/editor quality.",
            "Question date is supplied in the user input, as required for LongMemEval temporal questions.",
            "Benchmark users have no authorized knowledge base, so RAG is genuinely available but should return no evidence if called.",
            "The same DeepSeek model family answers and judges; this is official-prompt-compatible, not an official leaderboard result.",
        ],
    }


def summarize_scenario(rows: list[dict], budget: dict[str, int]) -> dict:
    non_abstention = [row for row in rows if not row["abstention"]]
    abstention = [row for row in rows if row["abstention"]]
    by_type = {
        question_type: {
            "total": len(type_rows),
            "qa_accuracy": mean(row["answer_correct"] for row in type_rows),
            "average_memory_calls": mean(row["memory_calls"] for row in type_rows),
        }
        for question_type in QUESTION_TYPES
        if (type_rows := [row for row in non_abstention if row["question_type"] == question_type])
    }
    return {
        "budget": budget,
        "total": len(rows),
        "qa_accuracy": mean(row["answer_correct"] for row in rows),
        "task_averaged_qa_accuracy": mean(item["qa_accuracy"] for item in by_type.values()),
        "abstention_accuracy": mean(row["answer_correct"] for row in abstention),
        "memory_recall_any": mean(row["memory_recall_any"] for row in non_abstention),
        "memory_recall_all": mean(row["memory_recall_all"] for row in non_abstention),
        "average_memory_recall": mean(row["memory_recall"] for row in non_abstention),
        "average_model_calls": mean(row["model_calls"] for row in rows),
        "average_tool_calls": mean(row["tool_calls"] for row in rows),
        "average_memory_calls": mean(row["memory_calls"] for row in rows),
        "average_rag_calls": mean(row["rag_calls"] for row in rows),
        "second_memory_call_rate": mean(row["memory_calls"] >= 2 for row in rows),
        "third_memory_call_rate": mean(row["memory_calls"] >= 3 for row in rows),
        "memory_query_change_rate_among_multi_call": mean(
            row["memory_query_changed"] for row in rows if row["memory_calls"] >= 2
        ),
        "rag_call_rate": mean(row["rag_calls"] > 0 for row in rows),
        "memory_no_progress_call_rate": mean(row["memory_no_progress_calls"] > 0 for row in rows),
        "model_budget_reached_count": sum(row["model_budget_reached"] for row in rows),
        "tool_budget_reached_count": sum(row["tool_budget_reached"] for row in rows),
        "memory_budget_reached_count": sum(row["memory_budget_reached"] for row in rows),
        "model_budget_violation_count": sum(row["model_calls"] > budget["model"] for row in rows),
        "tool_budget_violation_count": sum(row["tool_calls"] > budget["tool"] for row in rows),
        "memory_budget_violation_count": sum(row["memory_calls"] > budget["memory"] for row in rows),
        "rag_budget_violation_count": sum(row["rag_calls"] > budget["rag"] for row in rows),
        "failed_run_count": sum(row["status"] != "completed" for row in rows),
        "average_total_tokens": mean(row["total_tokens"] for row in rows),
        "average_latency_seconds": mean(row["latency_seconds"] for row in rows),
        "max_observed_model_calls": max((row["model_calls"] for row in rows), default=0),
        "max_observed_tool_calls": max((row["tool_calls"] for row in rows), default=0),
        "max_observed_memory_calls": max((row["memory_calls"] for row in rows), default=0),
        "by_type": by_type,
        "failures": [row["question_id"] for row in rows if not row["answer_correct"]],
    }


if __name__ == "__main__":
    raise SystemExit(main())
