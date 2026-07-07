from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.conversation import Conversation, Message
from app.db.models.feedback import Feedback
from app.schemas.feedback import FeedbackCreate, FeedbackRead


def create_feedback(db: Session, user_id: str, payload: FeedbackCreate) -> FeedbackRead:
    message = db.get(Message, payload.message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    conversation = db.get(Conversation, message.conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    if message.role != "assistant":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only assistant messages can be rated")

    existing = db.scalar(
        select(Feedback).where(
            Feedback.user_id == user_id,
            Feedback.message_id == message.id,
        )
    )
    if existing:
        existing.rating = payload.rating
        existing.reason = normalize_reason(payload.reason)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return to_feedback_read(existing)

    feedback = Feedback(
        user_id=user_id,
        message_id=message.id,
        rating=payload.rating,
        reason=normalize_reason(payload.reason),
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return to_feedback_read(feedback)


def list_feedbacks(db: Session, user_id: str, message_id: str | None = None) -> list[FeedbackRead]:
    query = select(Feedback).where(Feedback.user_id == user_id)
    if message_id:
        query = query.where(Feedback.message_id == message_id)
    rows = db.scalars(query.order_by(Feedback.created_at.desc())).all()
    return [to_feedback_read(row) for row in rows]


def to_feedback_read(feedback: Feedback) -> FeedbackRead:
    return FeedbackRead(
        id=feedback.id,
        user_id=feedback.user_id,
        message_id=feedback.message_id,
        rating=feedback.rating,
        reason=feedback.reason,
        created_at=feedback.created_at,
    )


def normalize_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    normalized = reason.strip()
    return normalized or None
