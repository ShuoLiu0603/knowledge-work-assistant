from __future__ import annotations

from typing import Any
from urllib import request

import redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine
from app.rag.vector_store import get_qdrant_client


HealthCheck = dict[str, str]


def build_readiness_report() -> dict[str, Any]:
    checks = {
        "database": check_database(),
        "redis": check_redis(),
        "qdrant": check_qdrant(),
        "minio": check_minio(),
        "worker": check_worker(),
    }
    is_ready = all(check["status"] == "ok" for check in checks.values())
    return {
        "status": "ok" if is_ready else "degraded",
        "checks": checks,
    }


def check_database() -> HealthCheck:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return ok()
    except Exception as exc:
        return failed(exc)


def check_redis() -> HealthCheck:
    client = None
    try:
        settings = get_settings()
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        client.ping()
        return ok()
    except Exception as exc:
        return failed(exc)
    finally:
        if client is not None:
            client.close()


def check_qdrant() -> HealthCheck:
    try:
        get_qdrant_client(timeout=get_settings().healthcheck_timeout_seconds).get_collections()
        return ok()
    except Exception as exc:
        return failed(exc)


def check_minio() -> HealthCheck:
    settings = get_settings()
    scheme = "https" if settings.minio_secure else "http"
    endpoint = settings.minio_endpoint
    base_url = endpoint if endpoint.startswith(("http://", "https://")) else f"{scheme}://{endpoint}"
    try:
        request.urlopen(
            f"{base_url.rstrip('/')}/minio/health/live",
            timeout=settings.healthcheck_timeout_seconds,
        ).close()
        return ok()
    except Exception as exc:
        return failed(exc)


def check_worker() -> HealthCheck:
    try:
        from app.workers.celery_app import celery_app

        responses = (
            celery_app.control.broadcast(
                "ping",
                reply=True,
                timeout=get_settings().healthcheck_timeout_seconds,
                limit=1,
            )
            or []
        )
        if not responses:
            raise RuntimeError("No Celery workers responded")
        return ok()
    except Exception as exc:
        return failed(exc)


def ok() -> HealthCheck:
    return {"status": "ok"}


def failed(exc: Exception) -> HealthCheck:
    return {
        "status": "error",
        "detail": f"{exc.__class__.__name__}: {str(exc)[:160]}",
    }
