from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import (
    AuthResponse,
    LogoutRequest,
    RefreshTokenRequest,
    UserCreate,
    UserLogin,
    UserRead,
)
from app.services.auth_service import (
    authenticate_user,
    issue_tokens,
    refresh_tokens,
    register_user,
    revoke_refresh_token,
)

router = APIRouter()


@router.post("/auth/register", response_model=AuthResponse)
def register(payload: UserCreate, db: Annotated[Session, Depends(get_db)]) -> AuthResponse:
    return register_user(db, payload)


@router.post("/auth/login", response_model=AuthResponse)
def login(payload: UserLogin, db: Annotated[Session, Depends(get_db)]) -> AuthResponse:
    user = authenticate_user(db, payload.email, payload.password)
    return issue_tokens(db, user)


@router.post("/auth/refresh", response_model=AuthResponse)
def refresh(payload: RefreshTokenRequest, db: Annotated[Session, Depends(get_db)]) -> AuthResponse:
    return refresh_tokens(db, payload.refresh_token)


@router.post("/auth/logout")
def logout(payload: LogoutRequest, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    revoke_refresh_token(db, payload.refresh_token)
    return {"status": "ok"}


@router.get("/me", response_model=UserRead)
def me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user
