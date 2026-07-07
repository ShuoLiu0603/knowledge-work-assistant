from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.qa import AskKnowledgeBaseRequest, AskKnowledgeBaseResponse
from app.services.qa_service import ask_knowledge_base

router = APIRouter(prefix="/knowledge-bases")


@router.post("/{kb_id}/ask", response_model=AskKnowledgeBaseResponse)
def ask_item(
    kb_id: str,
    payload: AskKnowledgeBaseRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AskKnowledgeBaseResponse:
    return ask_knowledge_base(db, current_user.id, kb_id, payload)
