from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.conversation import ConversationCreate, ConversationRead, MessageRead, StreamMessageRequest
from app.services.conversation_service import (
    create_conversation,
    delete_conversation,
    get_conversation_detail,
    list_conversations,
    list_messages,
    stream_message_response,
)

router = APIRouter(prefix="/conversations")


@router.get("", response_model=list[ConversationRead])
def list_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    knowledge_base_id: Annotated[str | None, Query()] = None,
    search_scope: Annotated[str | None, Query()] = None,
) -> list[ConversationRead]:
    return list_conversations(db, current_user.id, knowledge_base_id, search_scope)


@router.post("", response_model=ConversationRead, status_code=201)
def create_item(
    payload: ConversationCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ConversationRead:
    return create_conversation(db, current_user.id, payload)


@router.get("/{conversation_id}", response_model=ConversationRead)
def get_item(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ConversationRead:
    return get_conversation_detail(db, current_user.id, conversation_id)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    delete_conversation(db, current_user.id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
def list_conversation_messages(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[MessageRead]:
    return list_messages(db, current_user.id, conversation_id)


@router.post("/{conversation_id}/messages/stream")
def stream_conversation_message(
    conversation_id: str,
    payload: StreamMessageRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    return StreamingResponse(
        stream_message_response(db, current_user.id, conversation_id, payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
