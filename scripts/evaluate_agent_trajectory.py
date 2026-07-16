from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "apps" / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.agents.runtime import (
    finish_tool_call,
    is_duplicate_tool_call,
    normalize_tool_query,
    run_agent_runtime,
)
from app.agents.state import AgentRunState
from app.core.config import get_settings
from app.rag.retrieval import RetrievedChunk


DEFAULT_OUTPUT = ROOT / ".run" / "p0" / "agent_trajectory_results.jsonl"
DEFAULT_SUMMARY = ROOT / ".run" / "p0" / "agent_trajectory_summary.json"
RUNTIME_PATH = BACKEND_PATH / "app" / "agents" / "runtime.py"
EVALUATION_VERSION = "controlled-agent-trajectory-v2"


def evaluation_cases() -> list[dict]:
    direct = [
        ("direct_math", "What is 17 plus 25?", ""),
        ("direct_greeting", "Hello! Please greet me briefly.", ""),
        ("direct_rewrite", "Rewrite this more politely: Send the report today.", ""),
        ("direct_translate", "Translate 'good morning' into Chinese.", ""),
        ("profile_name", "What is my name?", "Core profile: The user's name is Alice Chen."),
        ("profile_language", "Which language do I prefer for replies?", "Core profile: Preferred response language is Chinese."),
        ("profile_role", "What is my current role?", "Core profile: Current role is backend engineer."),
        ("context_answer", "What framework did I just say I use?", "Conversation summary: The user just said they use FastAPI."),
    ]
    cases = [
        make_case(case_id, "direct", question, [[]], memory_context=context)
        for case_id, question, context in direct
    ]

    memory_cases = [
        ("memory_drink", "What drink do I usually prefer?", "The user usually prefers jasmine tea."),
        ("memory_project", "What personal project was I working on last month?", "The user was building a personal finance tracker."),
        ("memory_decision", "Which database did I decide to use for my side project?", "The user decided to use PostgreSQL for the side project."),
        ("memory_event", "Where did I travel during my last vacation?", "The user travelled to Hangzhou during the last vacation."),
        ("memory_workflow", "How do I usually review pull requests?", "The user first checks tests, then security, then readability."),
    ]
    cases.extend(
        make_case(case_id, "memory", question, [["memory"]], memory_results=[answer])
        for case_id, question, answer in memory_cases
    )

    rag_cases = [
        ("rag_hotel", "What is the company's hotel reimbursement limit?", "The hotel reimbursement limit is 600 CNY per night."),
        ("rag_invoice", "Which fields are required on a reimbursement invoice?", "Invoices must include the tax ID, amount, and issue date."),
        ("rag_leave", "How many annual leave days does company policy provide?", "Company policy provides 10 annual leave days."),
        ("rag_security", "What is the internal rule for confidential documents?", "Confidential documents require security level 3 or above."),
        ("rag_onboarding", "What is the first step in the internal onboarding procedure?", "The first onboarding step is identity verification."),
    ]
    cases.extend(
        make_case(case_id, "rag", question, [["rag"]], rag_batches=[[answer]])
        for case_id, question, answer in rag_cases
    )

    cases.extend(
        [
            make_case(
                "memory_miss_pet",
                "memory_miss",
                "What was the name of my childhood pet?",
                [["memory"]],
                memory_results=[],
            ),
            make_case(
                "memory_miss_school",
                "memory_miss",
                "Which primary school did I attend?",
                [["memory"]],
                memory_results=[],
            ),
        ]
    )

    cases.extend(
        [
            make_case(
                "memory_rag_meal",
                "memory_rag",
                "Considering my dietary preference, which dinner option complies with the company meal policy?",
                [["memory", "rag"], ["rag", "memory"]],
                memory_results=["The user is vegetarian."],
                rag_batches=[["The company meal policy reimburses vegetarian set meals up to 80 CNY."]],
            ),
            make_case(
                "memory_rag_travel",
                "memory_rag",
                "Considering my seat preference, which flight class may I book under company travel policy?",
                [["memory", "rag"], ["rag", "memory"]],
                memory_results=["The user prefers aisle seats."],
                rag_batches=[["Company travel policy permits economy class; aisle seats may be selected when available."]],
            ),
            make_case(
                "memory_rag_schedule",
                "memory_rag",
                "Use my usual work schedule and the company overtime rule to tell me when overtime begins.",
                [["memory", "rag"], ["rag", "memory"]],
                memory_results=["The user normally works from 09:00 to 18:00."],
                rag_batches=[["Overtime begins after an employee's normal scheduled working hours end."]],
            ),
        ]
    )

    cases.extend(
        [
            make_case(
                "multi_rag_exception",
                "multi_rag",
                "State the standard hotel limit and the exception approval rule.",
                [["rag", "rag"]],
                rag_batches=[
                    ["The standard hotel limit is 600 CNY per night. The exception rule is not in this section."],
                    ["Hotel expenses above the limit require written approval from the finance director."],
                ],
                require_distinct_queries=True,
            ),
            make_case(
                "multi_rag_compare",
                "multi_rag",
                "Compare the probation leave rule with the rule after confirmation.",
                [["rag", "rag"]],
                rag_batches=[
                    ["During probation, employees receive one day of paid personal leave."],
                    ["After confirmation, employees receive three days of paid personal leave."],
                ],
                require_distinct_queries=True,
            ),
        ]
    )
    return cases


def make_case(
    case_id: str,
    category: str,
    question: str,
    accepted_sequences: list[list[str]],
    *,
    memory_context: str = "",
    memory_results: list[str] | None = None,
    rag_batches: list[list[str]] | None = None,
    require_distinct_queries: bool = False,
) -> dict:
    return {
        "id": case_id,
        "category": category,
        "question": question,
        "accepted_sequences": accepted_sequences,
        "memory_context": memory_context,
        "memory_results": memory_results or [],
        "rag_batches": rag_batches or [],
        "require_distinct_queries": require_distinct_queries,
    }


def run_case(case: dict) -> dict:
    settings = get_settings()
    state = AgentRunState(
        user_id="trajectory-eval-user",
        knowledge_base_id="trajectory-eval-kb",
        input=case["question"],
        memory_context=case["memory_context"],
        memory_enabled=True,
        defer_memory_update=True,
    )
    started = time.perf_counter()

    def fake_memory(_db, current: AgentRunState, query: str) -> str:
        normalized = normalize_tool_query(query)
        if is_duplicate_tool_call(current, "memory", normalized):
            return finish_tool_call(
                current,
                {"tool": "memory", "query": normalized, "status": "duplicate", "error": "duplicate", "results": []},
            )
        results = list(case["memory_results"])
        current.memory_queries.append(normalized)
        current.memory_tool_call_count += 1
        current.memory_recalled = True
        if results:
            rows = [f"- {content}" for content in results]
            current.memory_context = "\n".join(filter(None, [current.memory_context, "Recalled memory:", *rows]))
        return finish_tool_call(
            current,
            {
                "tool": "memory",
                "query": normalized,
                "status": "success",
                "result_count": len(results),
                "new_result_count": len(results),
                "duplicate_result_count": 0,
                "results": [{"id": f"memory-{index}", "content": value} for index, value in enumerate(results)],
            },
        )

    def fake_rag(_db, current: AgentRunState, query: str) -> str:
        normalized = normalize_tool_query(query)
        if is_duplicate_tool_call(current, "rag", normalized):
            return finish_tool_call(
                current,
                {"tool": "rag", "query": normalized, "status": "duplicate", "error": "duplicate", "results": []},
            )
        batch_index = current.rag_tool_call_count
        batches = case["rag_batches"]
        results = batches[min(batch_index, len(batches) - 1)] if batches else []
        new_chunks = [
            RetrievedChunk(
                chunk_id=f"{case['id']}-rag-{batch_index}-{index}",
                document_id=f"{case['id']}-document-{batch_index}-{index}",
                knowledge_base_id="trajectory-eval-kb",
                chunk_index=index,
                content=content,
                score=1.0,
                file_name=f"controlled-{case['id']}.md",
                title_path=None,
                page_number=None,
                section_name=None,
                metadata={},
                security_level=1,
                retrieval_routes=["controlled"],
            )
            for index, content in enumerate(results)
        ]
        current.rag_queries.append(normalized)
        current.rag_tool_call_count += 1
        current.rag_searched = True
        current.rag_chunks.extend(new_chunks)
        current.rag_batches.append([chunk.chunk_id for chunk in new_chunks])
        return finish_tool_call(
            current,
            {
                "tool": "rag",
                "query": normalized,
                "status": "success",
                "result_count": len(new_chunks),
                "new_result_count": len(new_chunks),
                "duplicate_result_count": 0,
                "results": [
                    {
                        "citation": f"[{len(current.rag_chunks) - len(new_chunks) + index + 1}]",
                        "chunk_id": chunk.chunk_id,
                        "file_name": chunk.file_name,
                        "content": chunk.content,
                    }
                    for index, chunk in enumerate(new_chunks)
                ],
            },
        )

    error = None
    try:
        with (
            patch("app.agents.runtime.record_model_response", lambda *_args, **_kwargs: None),
            patch("app.agents.runtime.execute_memory_tool", fake_memory),
            patch("app.agents.runtime.execute_rag_tool", fake_rag),
        ):
            run_agent_runtime(None, state)
    except Exception as exc:
        error = str(exc)

    actual = [value.split(":", 1)[0] for value in state.executed_tool_calls]
    accepted = case["accepted_sequences"]
    distinct_queries = len(state.rag_queries) == len({value.casefold() for value in state.rag_queries})
    trajectory_correct = actual in accepted and (not case["require_distinct_queries"] or distinct_queries)
    sequence_metrics = tool_sequence_metrics(actual, accepted)
    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "accepted_sequences": accepted,
        "actual_sequence": actual,
        "trajectory_correct": trajectory_correct,
        **sequence_metrics,
        "distinct_rag_queries": distinct_queries,
        "memory_queries": list(state.memory_queries),
        "rag_queries": list(state.rag_queries),
        "model_calls": state.model_call_count,
        "tool_calls": state.tool_call_count,
        "within_budget": (
            state.model_call_count <= settings.agent_max_model_calls
            and state.tool_call_count <= settings.agent_max_tool_calls
        ),
        "answer": state.answer,
        "error": error,
        "latency_seconds": round(time.perf_counter() - started, 3),
        "model": settings.llm_model,
        "evaluation_fingerprint": case["evaluation_fingerprint"],
    }


def tool_sequence_metrics(actual: list[str], accepted: list[list[str]]) -> dict[str, bool | int]:
    expected_call_counts = {len(value) for value in accepted}
    return {
        "tool_set_correct": set(actual) in [set(value) for value in accepted],
        "tool_multiset_correct": Counter(actual) in [Counter(value) for value in accepted],
        "tool_count_correct": len(actual) in expected_call_counts,
        "extra_tool_call_count": max(0, len(actual) - max(expected_call_counts, default=0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate real-model Agent tool trajectories with controlled tools.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    model = get_settings().llm_model
    cases = evaluation_cases()
    for case in cases:
        case["evaluation_fingerprint"] = case_fingerprint(case, model)
    cached = {} if args.force else load_jsonl(args.output)
    pending = [
        case
        for case in cases
        if cached.get(case["id"], {}).get("evaluation_fingerprint") != case["evaluation_fingerprint"]
    ]
    results = dict(cached)
    if pending:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run_case, case): case["id"] for case in pending}
            with args.output.open("a", encoding="utf-8") as handle:
                for index, future in enumerate(as_completed(futures), start=1):
                    result = future.result()
                    results[result["id"]] = result
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                    handle.flush()
                    print(f"Agent cases: {index}/{len(pending)}", flush=True)
    ordered = [results[case["id"]] for case in cases]
    summary = summarize(ordered)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def load_jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["id"]: row for row in rows}


def summarize(results: list[dict]) -> dict:
    total = len(results)
    by_category = {}
    for category in sorted({row["category"] for row in results}):
        rows = [row for row in results if row["category"] == category]
        by_category[category] = {
            "total": len(rows),
            "trajectory_accuracy": mean(row["trajectory_correct"] for row in rows),
            "tool_set_accuracy": mean(row["tool_set_correct"] for row in rows),
            "tool_multiset_accuracy": mean(row["tool_multiset_correct"] for row in rows),
        }
    return {
        "benchmark": "controlled_agent_trajectory",
        "evaluation_version": EVALUATION_VERSION,
        "model": results[0]["model"] if results else "unknown",
        "total": total,
        "trajectory_accuracy": mean(row["trajectory_correct"] for row in results),
        "tool_set_accuracy": mean(row["tool_set_correct"] for row in results),
        "tool_multiset_accuracy": mean(row["tool_multiset_correct"] for row in results),
        "tool_count_accuracy": mean(row["tool_count_correct"] for row in results),
        "average_extra_tool_calls": mean(row["extra_tool_call_count"] for row in results),
        "completion_rate": mean(not row["error"] and bool(row["answer"]) for row in results),
        "budget_compliance": mean(row["within_budget"] for row in results),
        "unnecessary_tool_rate_on_direct": mean(
            bool(row["actual_sequence"]) for row in results if row["category"] == "direct"
        ),
        "average_model_calls": mean(row["model_calls"] for row in results),
        "average_tool_calls": mean(row["tool_calls"] for row in results),
        "by_category": by_category,
        "failures": [row for row in results if not row["trajectory_correct"]],
        "limitations": [
            "Single run per case; no repeated-run stability estimate.",
            "Memory and RAG observations are controlled so this measures orchestration, not retrieval quality.",
            "Cases are project-specific and are not a public benchmark.",
        ],
    }


def case_fingerprint(case: dict, model: str) -> str:
    payload = {
        "version": EVALUATION_VERSION,
        "model": model,
        "runtime_sha256": hashlib.sha256(RUNTIME_PATH.read_bytes()).hexdigest(),
        "case": case,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def mean(values) -> float:
    numbers = [float(value) for value in values]
    return round(sum(numbers) / len(numbers), 6) if numbers else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
