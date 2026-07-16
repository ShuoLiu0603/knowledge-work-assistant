from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "apps" / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.core.config import get_settings
from app.llm.provider import LlmMessage, get_llm_provider
from app.llm.structured_outputs import parse_json_value


SOURCE_ROOT = "https://cdn.jsdelivr.net/gh/Koki-Itai/LIT-RAGBench@main"
DEFAULT_DATA_DIR = ROOT / ".run" / "public-evals" / "LIT-RAGBench"
DEFAULT_OUTPUT = ROOT / ".run" / "p0" / "lit_ragbench_reader_results.jsonl"
DEFAULT_SUMMARY = ROOT / ".run" / "p0" / "lit_ragbench_reader_summary.json"
EVALUATION_VERSION = "lit-ragbench-reader-v1"
LICENSE = "CC BY-SA 4.0"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the configured reader with the official LIT-RAGBench English protocol."
    )
    parser.add_argument("--limit", type=int, default=0, help="Run the first N cases; zero runs all cases.")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    ensure_data(args.data_dir)
    cases = load_cases(args.data_dir)
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in selected]
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        parser.error("No LIT-RAGBench cases were selected")

    model = get_settings().llm_model
    for case in cases:
        case["evaluation_fingerprint"] = case_fingerprint(case, model)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cached = {} if args.force else load_jsonl(args.output)
    pending = [
        case
        for case in cases
        if cached.get(case["case_id"], {}).get("evaluation_fingerprint")
        != case["evaluation_fingerprint"]
    ]
    results = dict(cached)
    if pending:
        mode = "w" if args.force else "a"
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run_case, case): case["case_id"] for case in pending}
            with args.output.open(mode, encoding="utf-8") as handle:
                for index, future in enumerate(as_completed(futures), start=1):
                    result = future.result()
                    results[result["case_id"]] = result
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                    handle.flush()
                    print(
                        f"LIT-RAGBench Reader: {index}/{len(pending)} "
                        f"(correct={result['correct']})",
                        flush=True,
                    )

    ordered = [results[case["case_id"]] for case in cases]
    summary = summarize(ordered)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


def ensure_data(data_dir: Path) -> None:
    targets = {
        data_dir / "datasets" / "en.jsonl": f"{SOURCE_ROOT}/datasets/en.jsonl",
        data_dir / "prompts" / "evaluation" / "generate_en.txt": (
            f"{SOURCE_ROOT}/prompts/evaluation/generate_en.txt"
        ),
        data_dir / "prompts" / "evaluation" / "judge_en.txt": (
            f"{SOURCE_ROOT}/prompts/evaluation/judge_en.txt"
        ),
    }
    for target, source in targets.items():
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(source, target)


def load_cases(data_dir: Path) -> list[dict]:
    data_path = data_dir / "datasets" / "en.jsonl"
    generate_prompt = (data_dir / "prompts" / "evaluation" / "generate_en.txt").read_text(
        encoding="utf-8"
    )
    judge_prompt = (data_dir / "prompts" / "evaluation" / "judge_en.txt").read_text(
        encoding="utf-8"
    )
    rows = [
        json.loads(line)
        for line in data_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [prepare_case(index, row, generate_prompt, judge_prompt) for index, row in enumerate(rows)]


def prepare_case(index: int, row: dict, generate_prompt: str, judge_prompt: str) -> dict:
    positive = [normalize_chunk(item, True, chunk_index) for chunk_index, item in enumerate(row["positive_chunk_list"])]
    negative = [normalize_chunk(item, False, chunk_index) for chunk_index, item in enumerate(row["negative_chunk_list"])]
    documents = positive + negative
    random.Random(42 + index).shuffle(documents)
    qa_types = [str(value) for value in row.get("qa_type", [])]
    return {
        "case_id": f"lit-{index:03d}",
        "source_index": index,
        "query": str(row["question"]),
        "answer": str(row["answer"]),
        "qa_types": qa_types,
        "family": case_family(qa_types),
        "reasoning_content": str(row.get("reasoning_content", "")),
        "positive_context": [item["text"] for item in positive],
        "documents": documents,
        "generate_prompt": generate_prompt,
        "judge_prompt": judge_prompt,
    }


def normalize_chunk(item: dict | str, positive: bool, positive_index: int) -> dict:
    if isinstance(item, dict):
        title = str(item.get("title", "")).strip()
        content = str(item.get("content", "")).strip()
    else:
        title = ""
        content = str(item).strip()
    text = f"{title}\n{content}".strip() if title else content
    return {
        "text": text,
        "positive": positive,
        "positive_index": positive_index if positive else None,
    }


def case_family(qa_types: list[str]) -> str:
    prefixes = {value.split("_", 1)[0] for value in qa_types}
    if "A" in prefixes:
        return "abstention"
    if "I" in prefixes:
        return "integration"
    if "R" in prefixes:
        return "reasoning"
    if "L" in prefixes:
        return "logic"
    if "T" in prefixes:
        return "table"
    return "other"


def run_case(case: dict) -> dict:
    provider = get_llm_provider()
    numbered_documents = "\n".join(
        f"[cite:{index}] {document['text']}"
        for index, document in enumerate(case["documents"], start=1)
    )
    user_prompt = (
        f"<DOCUMENTS>\n{numbered_documents}\n</DOCUMENTS>\n\n"
        f"<QUESTION>\n{case['query']}\n</QUESTION>"
    )
    started = time.perf_counter()
    prediction = ""
    error = None
    judge_error = None
    completion = None
    judgment = {"score": 0, "evaluation_reason": ""}
    try:
        completion = provider.complete_with_metadata(
            [
                LlmMessage("system", case["generate_prompt"]),
                LlmMessage("user", user_prompt),
            ],
            temperature=0.0,
        )
        prediction = completion.content.strip()
    except Exception as exc:
        error = str(exc)

    if not error:
        try:
            judgment = judge_answer(provider, case, prediction)
        except Exception as exc:
            judge_error = str(exc)

    return {
        "evaluation_version": EVALUATION_VERSION,
        "case_id": case["case_id"],
        "source_index": case["source_index"],
        "qa_types": case["qa_types"],
        "family": case["family"],
        "question": case["query"],
        "reference_answer": case["answer"],
        "prediction": prediction,
        "correct": int(judgment.get("score", 0)) == 1,
        "evaluation_reason": str(judgment.get("evaluation_reason", "")),
        "positive_chunk_count": len(case["positive_context"]),
        "document_count": len(case["documents"]),
        "error": error,
        "judge_error": judge_error,
        "prompt_tokens": completion.prompt_tokens if completion else 0,
        "completion_tokens": completion.completion_tokens if completion else 0,
        "latency_seconds": round(time.perf_counter() - started, 3),
        "model": get_settings().llm_model,
        "evaluation_fingerprint": case["evaluation_fingerprint"],
    }


def judge_answer(provider, case: dict, prediction: str) -> dict:
    output_format = {
        "score": "Score to evaluate the accuracy of the answer (int, 0 or 1)",
        "evaluation_reason": "String explaining the reason for the evaluation (str)",
    }
    system_prompt = (
        f"{case['judge_prompt']}\n<OUTPUT_FORMAT>\n"
        f"Output only the following JSON object:\n{json.dumps(output_format, ensure_ascii=False)}\n"
        "</OUTPUT_FORMAT>"
    )
    user_prompt = (
        f"<QUESTION>\n{case['query']}\n</QUESTION>\n\n"
        f"<REFERENCE_REASONING_CONTENT>\n{case['reasoning_content']}\n"
        "</REFERENCE_REASONING_CONTENT>\n\n"
        f"<REFERENCE_ANSWER>\n{case['answer']}\n</REFERENCE_ANSWER>\n\n"
        f"<POSITIVE_CONTEXT>\n{'\n'.join(case['positive_context'])}\n</POSITIVE_CONTEXT>\n\n"
        f"<GENERATED_ANSWER>\n{prediction}\n</GENERATED_ANSWER>"
    )
    completion = provider.complete_with_metadata(
        [LlmMessage("system", system_prompt), LlmMessage("user", user_prompt)],
        temperature=0.0,
    )
    parsed = parse_json_value(completion.content)
    if not isinstance(parsed, dict):
        raise ValueError("LIT-RAGBench judge did not return a JSON object")
    return parsed


def summarize(results: list[dict]) -> dict:
    families = sorted({row["family"] for row in results})
    tags = sorted({tag for row in results for tag in row["qa_types"]})
    return {
        "benchmark": "LIT-RAGBench English Reader",
        "evaluation_version": EVALUATION_VERSION,
        "license": LICENSE,
        "model": results[0]["model"] if results else "unknown",
        "total": len(results),
        "accuracy": mean(row["correct"] for row in results),
        "answerable_accuracy": mean(
            row["correct"] for row in results if row["family"] != "abstention"
        ),
        "abstention_accuracy": mean(
            row["correct"] for row in results if row["family"] == "abstention"
        ),
        "average_prompt_tokens": mean(row["prompt_tokens"] for row in results),
        "average_completion_tokens": mean(row["completion_tokens"] for row in results),
        "average_latency_seconds": mean(row["latency_seconds"] for row in results),
        "failed_run_count": sum(bool(row["error"]) for row in results),
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
            "Uses the official English generation and judge prompts with DeepSeek instead of the paper's GPT-4.1 models.",
            "This Reader protocol receives all supplied chunks and does not evaluate retrieval or Agent routing.",
            "Some English examples retain non-English source chunks from the released dataset.",
        ],
    }


def summarize_group(rows: list[dict]) -> dict:
    return {"total": len(rows), "accuracy": mean(row["correct"] for row in rows)}


def mean(values) -> float:
    items = [float(value) for value in values]
    return round(sum(items) / len(items), 6) if items else 0.0


def case_fingerprint(case: dict, model: str) -> str:
    payload = {
        "version": EVALUATION_VERSION,
        "model": model,
        "query": case["query"],
        "answer": case["answer"],
        "qa_types": case["qa_types"],
        "reasoning_content": case["reasoning_content"],
        "documents": case["documents"],
        "generate_prompt": case["generate_prompt"],
        "judge_prompt": case["judge_prompt"],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {row["case_id"]: row for row in rows}


if __name__ == "__main__":
    raise SystemExit(main())
