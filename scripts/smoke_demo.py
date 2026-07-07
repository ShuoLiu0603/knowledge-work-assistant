from __future__ import annotations

import argparse
import json
import mimetypes
import time
import uuid
from pathlib import Path
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]


class SmokeError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an end-to-end demo smoke test against a running stack.")
    parser.add_argument("--base-url", default="http://localhost:8000/api", help="Backend API base URL.")
    parser.add_argument("--demo-file", default=str(ROOT / "demo" / "company_policy_demo.md"))
    parser.add_argument("--question", default="住宿报销上限是多少？")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--keep-data", action="store_true", help="Keep the created knowledge base after the run.")
    args = parser.parse_args()

    client = ApiClient(args.base_url.rstrip("/"))
    kb_id = None

    try:
        print("Waiting for backend readiness...", flush=True)
        wait_for_ready(client, args.timeout_seconds)

        email = f"smoke-{uuid.uuid4().hex[:10]}@example.com"
        password = "Password123!"
        print(f"Registering smoke user {email}...", flush=True)
        auth = client.post(
            "/auth/register",
            {
                "email": email,
                "username": "Smoke Demo",
                "password": password,
            },
        )
        client.token = auth["access_token"]

        print("Creating knowledge base...", flush=True)
        kb = client.post(
            "/knowledge-bases",
            {
                "name": f"Smoke Demo KB {uuid.uuid4().hex[:6]}",
                "description": "Temporary knowledge base created by scripts/smoke_demo.py.",
                "visibility": "private",
            },
        )
        kb_id = kb["id"]

        demo_file = Path(args.demo_file)
        print(f"Uploading {demo_file.name}...", flush=True)
        upload = client.upload_file(f"/knowledge-bases/{kb_id}/documents", "file", demo_file)
        document_id = upload["document_id"]

        print("Waiting for document indexing...", flush=True)
        document = wait_for_document_indexed(client, document_id, args.timeout_seconds)
        if int(document.get("chunk_count") or 0) <= 0:
            raise SmokeError("Indexed document has no chunks")

        chunks = client.get(f"/documents/{document_id}/chunks")
        if not chunks:
            raise SmokeError("Chunk endpoint returned an empty list")

        print("Asking demo question...", flush=True)
        answer = client.post(f"/knowledge-bases/{kb_id}/ask", {"question": args.question, "top_k": 5})
        citations = answer.get("citations") or []
        if not answer.get("answer"):
            raise SmokeError("Ask endpoint returned an empty answer")
        if not citations:
            raise SmokeError("Ask endpoint returned no citations")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "knowledge_base_id": kb_id,
                    "document_id": document_id,
                    "chunk_count": document["chunk_count"],
                    "citation_count": len(citations),
                    "answer_preview": answer["answer"][:160],
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        return 0
    finally:
        if kb_id and not args.keep_data:
            try:
                print("Cleaning up smoke knowledge base...", flush=True)
                client.delete(f"/knowledge-bases/{kb_id}")
            except Exception as exc:
                print(f"Cleanup skipped after error: {exc}", flush=True)


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.token: str | None = None

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        return self.request("POST", path, body, {"Content-Type": "application/json"})

    def delete(self, path: str) -> None:
        self.request("DELETE", path)

    def upload_file(self, path: str, field_name: str, file_path: Path):
        boundary = f"----agentic-rag-smoke-{uuid.uuid4().hex}"
        filename = file_path.name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        file_bytes = file_path.read_bytes()
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                file_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        return self.request("POST", path, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None):
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"

        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                response_body = response.read()
                if response.status == 204 or not response_body:
                    return None
                return json.loads(response_body.decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SmokeError(f"{method} {path} failed with {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise SmokeError(f"{method} {path} failed: {exc.reason}") from exc


def wait_for_ready(client: ApiClient, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            report = client.get("/ready")
            if report.get("status") == "ok":
                return
            last_error = json.dumps(report, ensure_ascii=False)
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise SmokeError(f"Backend did not become ready within {timeout_seconds}s: {last_error}")


def wait_for_document_indexed(client: ApiClient, document_id: str, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_document: dict | None = None
    while time.monotonic() < deadline:
        document = client.get(f"/documents/{document_id}")
        last_document = document
        status = document.get("status")
        if status == "indexed":
            return document
        if status == "failed":
            raise SmokeError(f"Document indexing failed: {document.get('error_message')}")
        time.sleep(3)
    raise SmokeError(f"Document did not become indexed within {timeout_seconds}s: {last_document}")


if __name__ == "__main__":
    raise SystemExit(main())
