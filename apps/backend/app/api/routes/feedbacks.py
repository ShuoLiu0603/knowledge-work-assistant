from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.feedback import FeedbackCreate, FeedbackRead
from app.services.feedback_service import create_feedback, list_feedbacks

router = APIRouter(prefix="/feedbacks")


@router.get("", response_model=list[FeedbackRead])
def list_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    message_id: Annotated[str | None, Query()] = None,
) -> list[FeedbackRead]:
    return list_feedbacks(db, current_user.id, message_id)


@router.post("", response_model=FeedbackRead, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: FeedbackCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FeedbackRead:
    return create_feedback(db, current_user.id, payload)
