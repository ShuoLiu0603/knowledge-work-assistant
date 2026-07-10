from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.agent import AgentRunRead, AgentRunRequest
from app.services.agent_service import get_agent_run, list_agent_runs, run_agent, to_agent_run_read

router = APIRouter(prefix="/agent-runs")


@router.get("", response_model=list[AgentRunRead])
def list_items(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    knowledge_base_id: Annotated[str | None, Query()] = None,
    conversation_id: Annotated[str | None, Query()] = None,
    message_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AgentRunRead]:
    return list_agent_runs(
        db,
        current_user.id,
        knowledge_base_id,
        conversation_id,
        message_id,
        limit,
        offset,
    )


@router.post("", response_model=AgentRunRead, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: AgentRunRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AgentRunRead:
    run = run_agent(
        db,
        current_user.id,
        payload.knowledge_base_id,
        payload.input,
        top_k=payload.top_k,
        search_scope=payload.search_scope,
        department_id=payload.department_id,
    )
    return to_agent_run_read(run)


@router.get("/{run_id}", response_model=AgentRunRead)
def get_item(
    run_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AgentRunRead:
    return get_agent_run(db, current_user.id, run_id)
