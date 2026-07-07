from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

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
            patch.object(health_module, "check_qdrant", return_value=ok),
            patch.object(health_module, "check_minio", return_value=ok),
            patch.object(health_module, "check_worker", return_value=ok),
        ):
            report = health_module.build_readiness_report()

        self.assertEqual(report["status"], "ok")
        self.assertEqual(set(report["checks"]), {"database", "redis", "qdrant", "minio", "worker"})

    def test_readiness_endpoint_returns_503_when_dependency_fails(self) -> None:
        report = {
            "status": "degraded",
            "checks": {
                "database": {"status": "ok"},
                "redis": {"status": "error", "detail": "ConnectionError"},
                "qdrant": {"status": "ok"},
                "minio": {"status": "ok"},
            },
        }
        with patch("app.api.routes.health.build_readiness_report", return_value=report):
            response = asyncio.run(readiness_check())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), report)


if __name__ == "__main__":
    unittest.main()
