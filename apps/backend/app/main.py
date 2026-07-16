from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings, validate_runtime_settings
from app.db.session import init_db
from app.memory.vector_index import ensure_memory_collection
from app.rag.vector_store import ensure_qdrant_collection

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    try:
        ensure_qdrant_collection()
    except RuntimeError as exc:
        logger.warning("Qdrant collection initialization skipped: %s", exc)
    try:
        ensure_memory_collection()
    except Exception as exc:
        logger.warning("Qdrant memory collection initialization skipped: %s", exc)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    validate_runtime_settings(settings)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "health": f"{settings.api_prefix}/health"}

    return app


app = create_app()
