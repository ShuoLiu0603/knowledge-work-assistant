from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "agentic_rag_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    timezone="UTC",
)

import app.workers.document_tasks  # noqa: E402,F401
import app.workers.memory_tasks  # noqa: E402,F401
