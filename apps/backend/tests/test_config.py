from __future__ import annotations

import unittest

from app.core.config import Settings, validate_runtime_settings


class ConfigTests(unittest.TestCase):
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

    def test_production_accepts_hardened_settings(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://user:pass@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="a-long-random-secret-value-for-prod",
            backend_cors_origins="https://app.example.com",
            llm_api_key="test-llm-key",
            embedding_api_key="test-embedding-key",
        )

        validate_runtime_settings(settings)

    def test_production_rejects_short_jwt_secret(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://user:pass@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="short-secret",
            backend_cors_origins="https://app.example.com",
            llm_api_key="test-llm-key",
            embedding_api_key="test-embedding-key",
        )

        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        self.assertIn("at least 32 bytes", str(error.exception))


if __name__ == "__main__":
    unittest.main()
