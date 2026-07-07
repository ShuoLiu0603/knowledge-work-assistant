from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = ROOT / "apps" / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

from app.evaluation.metrics import compute_metrics


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        cases = payload.get("cases", [])
    else:
        cases = payload
    if not isinstance(cases, list):
        raise ValueError("Evaluation dataset must be a list or an object with a cases list")
    return [case for case in cases if isinstance(case, dict)]


def login(base_url: str, email: str, password: str) -> str:
    response = post_json(
        f"{base_url.rstrip('/')}/auth/login",
        {"email": email, "password": password},
        token=None,
    )
    return str(response["access_token"])


def ask(base_url: str, token: str, kb_id: str, question: str, top_k: int) -> dict[str, Any]:
    return post_json(
        f"{base_url.rstrip('/')}/knowledge-bases/{kb_id}/ask",
        {"question": question, "top_k": top_k},
        token=token,
    )


def post_json(url: str, payload: dict[str, Any], token: str | None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    with request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def run_online(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(Path(args.dataset))
    token = args.token or login(args.base_url, args.email, args.password)
    results = [
        ask(args.base_url, token, args.kb_id, str(case["question"]), args.top_k)
        for case in cases
    ]
    return compute_metrics(cases, results, top_k=args.top_k)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAG retrieval and citation quality.")
    parser.add_argument("--dataset", default="demo/rag_eval_questions.json")
    parser.add_argument("--base-url", default="http://localhost:8000/api")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--email", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    if not args.token and (not args.email or not args.password):
        parser.error("Provide either --token or both --email and --password")

    report = run_online(args)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
