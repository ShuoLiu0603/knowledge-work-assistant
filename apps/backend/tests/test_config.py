from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.core.config import Settings, validate_runtime_settings


ROOT = Path(__file__).resolve().parents[3]
EXTERNAL_ENV_KEYS = {
    "APP_ENV_FILE",
    "NGINX_MAX_BODY_SIZE_MB",
    "NGINX_PROXY_TIMEOUT_SECONDS",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "VITE_API_BASE_URL",
}


def environment_keys(path: Path) -> list[str]:
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _value = stripped.split("=", 1)
        keys.append(key.strip())
    return keys


class ConfigTests(unittest.TestCase):
    def test_environment_templates_have_the_exact_supported_keys(self) -> None:
        expected = {field_name.upper() for field_name in Settings.model_fields} | EXTERNAL_ENV_KEYS
        template_orders: list[list[str]] = []
        for filename in (".env.example", ".env.production.example"):
            with self.subTest(filename=filename):
                keys = environment_keys(ROOT / filename)
                self.assertEqual(set(keys), expected)
                self.assertEqual(len(keys), len(set(keys)))
                template_orders.append(keys)

        self.assertEqual(template_orders[0], template_orders[1])

    def test_environment_templates_document_every_assignment(self) -> None:
        for filename in (".env.example", ".env.production.example"):
            with self.subTest(filename=filename):
                lines = (ROOT / filename).read_text(encoding="utf-8").splitlines()
                for index, line in enumerate(lines):
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    self.assertGreater(index, 0, msg=f"{filename}: {line}")
                    self.assertTrue(
                        lines[index - 1].startswith("# "),
                        msg=f"{filename}: missing comment for {line.split('=', 1)[0]}",
                    )

    def test_invalid_cross_parameter_relationships_are_rejected(self) -> None:
        invalid_values = (
            {"default_chunk_size": 100, "default_chunk_overlap": 100},
            {"conversation_summary_min_tokens": 501, "conversation_summary_trigger_tokens": 500},
            {
                "memory_context_profile_weight": 0,
                "memory_context_long_term_weight": 0,
                "memory_context_summary_weight": 0,
                "memory_context_recent_weight": 0,
            },
            {"celery_task_retry_backoff_seconds": 10, "celery_task_retry_backoff_max_seconds": 5},
            {"conversation_summary_min_messages": 31, "conversation_summary_max_unprocessed": 30},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Settings(**values)

    def test_invalid_choice_settings_are_rejected(self) -> None:
        invalid_values = (
            {"app_env": "prd"},
            {"memory_update_mode": "eventually"},
            {"jwt_algorithm": "none"},
            {"llm_provider": "unsupported"},
            {"embedding_provider": "unsupported"},
            {"backend_cors_origins": "not-an-origin"},
            {"api_prefix": "api"},
            {"api_prefix": "/api;include"},
            {"api_prefix": "/api/../v2"},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Settings(**values)

    def test_development_allows_default_settings(self) -> None:
        settings = Settings(app_env="development")

        validate_runtime_settings(settings)
        self.assertTrue(Settings.model_fields["memory_vector_index_enabled"].default)

    def test_production_rejects_insecure_defaults(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="sqlite+pysqlite:///./local.db",
            auto_create_tables=True,
            jwt_secret_key="change-me",
            backend_cors_origins="*",
            llm_api_key="",
            embedding_api_key="",
            minio_access_key="minioadmin",
            minio_secret_key="minioadmin",
        )

        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        message = str(error.exception)
        self.assertIn("JWT_SECRET_KEY", message)
        self.assertIn("DATABASE_URL", message)
        self.assertIn("AUTO_CREATE_TABLES", message)
        self.assertIn("BACKEND_CORS_ORIGINS", message)
        self.assertIn("LLM_API_KEY", message)
        self.assertIn("EMBEDDING_API_KEY", message)
        self.assertIn("MINIO_ACCESS_KEY", message)
        self.assertIn("MINIO_SECRET_KEY", message)

    def test_production_accepts_hardened_settings(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://user:strong-db-password@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="a-long-random-secret-value-for-prod",
            backend_cors_origins="https://app.company.com",
            llm_api_key="test-llm-key",
            embedding_api_key="test-embedding-key",
            minio_access_key="prod-minio-access",
            minio_secret_key="prod-minio-secret",
        )

        validate_runtime_settings(settings)

    def test_production_rejects_short_jwt_secret(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://user:strong-db-password@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="short-secret",
            backend_cors_origins="https://app.company.com",
            llm_api_key="test-llm-key",
            embedding_api_key="test-embedding-key",
            minio_access_key="prod-minio-access",
            minio_secret_key="prod-minio-secret",
        )

        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        self.assertIn("at least 32 bytes", str(error.exception))

    def test_production_environment_name_is_normalized(self) -> None:
        settings = Settings(
            app_env=" production ",
            database_url="sqlite+pysqlite:///./local.db",
            auto_create_tables=True,
        )

        self.assertEqual(settings.app_env, "production")
        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        self.assertIn("DATABASE_URL", str(error.exception))
        self.assertIn("AUTO_CREATE_TABLES", str(error.exception))

    def test_production_rejects_default_database_password_inside_url(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://rag_user:rag_password@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="a-long-random-secret-value-for-prod",
            backend_cors_origins="https://app.company.com",
            llm_api_key="test-llm-key",
            embedding_api_key="test-embedding-key",
            minio_access_key="prod-minio-access",
            minio_secret_key="prod-minio-secret",
        )

        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        self.assertIn("DATABASE_URL", str(error.exception))

    def test_production_rejects_invalid_memory_update_mode(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(memory_update_mode="eventually")

    def test_production_rejects_placeholder_cors_origin(self) -> None:
        for origin in ("https://your-rag.example.com", "https://app.test", "https://foo.localhost"):
            with self.subTest(origin=origin):
                settings = Settings(
                    app_env="production",
                    database_url="postgresql+psycopg://user:strong-db-password@postgres:5432/rag_app",
                    auto_create_tables=False,
                    jwt_secret_key="a-long-random-secret-value-for-prod",
                    backend_cors_origins=origin,
                    llm_api_key="test-llm-key",
                    embedding_api_key="test-embedding-key",
                    minio_access_key="prod-minio-access",
                    minio_secret_key="prod-minio-secret",
                )

                with self.assertRaises(RuntimeError) as error:
                    validate_runtime_settings(settings)

                self.assertIn("BACKEND_CORS_ORIGINS", str(error.exception))

    def test_production_rejects_non_https_cors_origin(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://user:strong-db-password@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="a-long-random-secret-value-for-prod",
            backend_cors_origins="http://app.company.com",
            llm_api_key="test-llm-key",
            embedding_api_key="test-embedding-key",
            minio_access_key="prod-minio-access",
            minio_secret_key="prod-minio-secret",
        )

        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        self.assertIn("HTTPS origins", str(error.exception))

    def test_production_rejects_placeholder_credentials(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://rag_user:replace-with-a-strong-password@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="replace-with-at-least-32-random-bytes",
            backend_cors_origins="https://app.company.com",
            llm_api_key="replace-with-your-llm-api-key",
            embedding_api_key="replace-with-your-embedding-api-key",
            minio_access_key="replace-with-a-strong-minio-access-key",
            minio_secret_key="replace-with-a-strong-minio-secret-key",
        )

        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        message = str(error.exception)
        self.assertIn("JWT_SECRET_KEY", message)
        self.assertIn("DATABASE_URL", message)
        self.assertIn("LLM_API_KEY", message)
        self.assertIn("EMBEDDING_API_KEY", message)
        self.assertIn("MINIO_ACCESS_KEY", message)
        self.assertIn("MINIO_SECRET_KEY", message)

    def test_celery_entrypoint_rejects_insecure_production_settings(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(ROOT / "apps" / "backend"),
                "APP_ENV": "production",
                "DATABASE_URL": "sqlite+pysqlite:///./unsafe.db",
                "AUTO_CREATE_TABLES": "true",
                "JWT_SECRET_KEY": "short",
                "BACKEND_CORS_ORIGINS": "*",
                "LLM_API_KEY": "replace-me",
                "EMBEDDING_API_KEY": "replace-me",
                "MINIO_ACCESS_KEY": "minioadmin",
                "MINIO_SECRET_KEY": "minioadmin",
            }
        )

        completed = subprocess.run(
            [sys.executable, "-c", "import app.workers.celery_app"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Invalid production settings", completed.stderr)


if __name__ == "__main__":
    unittest.main()
