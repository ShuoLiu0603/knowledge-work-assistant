from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.health import build_readiness_report

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check() -> JSONResponse:
    report = build_readiness_report()
    status_code = 200 if report["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=report)
