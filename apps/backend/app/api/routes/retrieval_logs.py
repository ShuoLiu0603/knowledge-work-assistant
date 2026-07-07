from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.retrieval_log import RetrievalLogRead
from app.services.retrieval_log_service import get_retrieval_log, list_retrieval_logs

router = APIRouter(prefix="/retrieval-logs")


@router.get("", response_model=list[RetrievalLogRead])
def list_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    knowledge_base_id: Annotated[str | None, Query()] = None,
    conversation_id: Annotated[str | None, Query()] = None,
    message_id: Annotated[str | None, Query()] = None,
) -> list[RetrievalLogRead]:
    return list_retrieval_logs(db, current_user.id, knowledge_base_id, conversation_id, message_id)


@router.get("/{log_id}", response_model=RetrievalLogRead)
def get_item(
    log_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RetrievalLogRead:
    return get_retrieval_log(db, current_user.id, log_id)
