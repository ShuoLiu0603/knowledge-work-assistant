from fastapi import APIRouter

from app.api.routes.admin import router as admin_router
from app.api.routes.agents import router as agents_router
from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.departments import router as departments_router
from app.api.routes.documents import router as documents_router
from app.api.routes.feedbacks import router as feedbacks_router
from app.api.routes.health import router as health_router
from app.api.routes.knowledge_bases import router as knowledge_bases_router
from app.api.routes.llm_logs import router as llm_logs_router
from app.api.routes.memories import router as memories_router
from app.api.routes.qa import router as qa_router
from app.api.routes.retrieval_logs import router as retrieval_logs_router

api_router = APIRouter()
api_router.include_router(admin_router, tags=["admin"])
api_router.include_router(agents_router, tags=["agent-runs"])
api_router.include_router(auth_router, tags=["auth"])
api_router.include_router(health_router, tags=["health"])
api_router.include_router(knowledge_bases_router, tags=["knowledge-bases"])
api_router.include_router(llm_logs_router, tags=["llm-logs"])
api_router.include_router(memories_router, tags=["memories"])
api_router.include_router(departments_router, tags=["departments"])
api_router.include_router(documents_router, tags=["documents"])
api_router.include_router(feedbacks_router, tags=["feedbacks"])
api_router.include_router(qa_router, tags=["qa"])
api_router.include_router(conversations_router, tags=["conversations"])
api_router.include_router(retrieval_logs_router, tags=["retrieval-logs"])
