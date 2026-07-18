from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.api.routes.health import health_check, readiness_check
from app.core import health as health_module


class HealthTests(unittest.TestCase):
    def test_health_check_is_process_liveness_only(self) -> None:
        self.assertEqual(asyncio.run(health_check()), {"status": "ok"})

    def test_readiness_report_is_ok_when_dependencies_are_ok(self) -> None:
        ok = {"status": "ok"}
        with (
            patch.object(health_module, "check_database", return_value=ok),
            patch.object(health_module, "check_redis", return_value=ok),
            patch.object(health_module, "check_pgvector", return_value=ok),
            patch.object(health_module, "check_minio", return_value=ok),
            patch.object(health_module, "check_worker", return_value=ok),
        ):
            report = health_module.build_readiness_report()

        self.assertEqual(report["status"], "ok")
        self.assertEqual(set(report["checks"]), {"database", "redis", "pgvector", "minio", "worker"})

    def test_readiness_endpoint_returns_503_when_dependency_fails(self) -> None:
        report = {
            "status": "degraded",
            "checks": {
                "database": {"status": "ok"},
                "redis": {"status": "error", "detail": "ConnectionError"},
                "pgvector": {"status": "ok"},
                "minio": {"status": "ok"},
            },
        }
        with patch("app.api.routes.health.build_readiness_report", return_value=report):
            response = asyncio.run(readiness_check())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), report)

    def test_worker_check_returns_after_first_celery_ping_reply(self) -> None:
        control = MagicMock()
        control.broadcast.return_value = [{"celery@worker": {"ok": "pong"}}]
        fake_app = SimpleNamespace(control=control)

        with (
            patch("app.core.health.get_settings", return_value=SimpleNamespace(healthcheck_timeout_seconds=3)),
            patch("app.workers.celery_app.celery_app", fake_app),
        ):
            self.assertEqual(health_module.check_worker(), {"status": "ok"})

        control.broadcast.assert_called_once_with("ping", reply=True, timeout=3, limit=1)


if __name__ == "__main__":
    unittest.main()
