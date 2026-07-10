from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError

from app.core.config import Settings, validate_runtime_settings


ROOT = Path(__file__).resolve().parents[3]


def environment_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _value = stripped.split("=", 1)
        keys.add(key.strip())
    return keys


class ConfigTests(unittest.TestCase):
    def test_environment_templates_cover_every_backend_setting(self) -> None:
        expected = {field_name.upper() for field_name in Settings.model_fields}
        for filename in (".env.example", ".env.production.example"):
            with self.subTest(filename=filename):
                keys = environment_keys(ROOT / filename)
                self.assertEqual(expected - keys, set())

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
            {"memory_recall_threshold_min": 0.6, "memory_recall_threshold_max": 0.5},
            {"celery_task_retry_backoff_seconds": 10, "celery_task_retry_backoff_max_seconds": 5},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Settings(**values)

    def test_development_allows_default_settings(self) -> None:
        settings = Settings(app_env="development")

        validate_runtime_settings(settings)

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
            backend_cors_origins="https://app.example.com",
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
            backend_cors_origins="https://app.example.com",
            llm_api_key="test-llm-key",
            embedding_api_key="test-embedding-key",
            minio_access_key="prod-minio-access",
            minio_secret_key="prod-minio-secret",
        )

        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        self.assertIn("at least 32 bytes", str(error.exception))

    def test_production_rejects_invalid_memory_update_mode(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://user:strong-db-password@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="a-long-random-secret-value-for-prod",
            backend_cors_origins="https://app.example.com",
            llm_api_key="test-llm-key",
            embedding_api_key="test-embedding-key",
            minio_access_key="prod-minio-access",
            minio_secret_key="prod-minio-secret",
            memory_update_mode="eventually",
        )

        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        self.assertIn("MEMORY_UPDATE_MODE", str(error.exception))

    def test_production_rejects_placeholder_credentials(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://rag_user:replace-with-a-strong-password@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="replace-with-at-least-32-random-bytes",
            backend_cors_origins="https://app.example.com",
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


if __name__ == "__main__":
    unittest.main()
