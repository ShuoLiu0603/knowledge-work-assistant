from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.llm_log import LlmCallLogRead
from app.services.llm_log_service import get_llm_call_log, list_llm_call_logs

router = APIRouter(prefix="/llm-logs")


@router.get("", response_model=list[LlmCallLogRead])
def list_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    conversation_id: Annotated[str | None, Query()] = None,
    agent_name: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[LlmCallLogRead]:
    return list_llm_call_logs(db, current_user.id, conversation_id, agent_name, limit)


@router.get("/{log_id}", response_model=LlmCallLogRead)
def get_item(
    log_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LlmCallLogRead:
    return get_llm_call_log(db, current_user.id, log_id)
