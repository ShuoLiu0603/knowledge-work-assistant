from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    utc_now,
    verify_password,
)
from app.db.models.refresh_token import RefreshToken
from app.db.models.user import User
from app.schemas.auth import AuthResponse, UserCreate


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def register_user(db: Session, payload: UserCreate) -> AuthResponse:
    if get_user_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )

    is_first_user = int(db.scalar(select(func.count(User.id))) or 0) == 0
    user = User(
        email=payload.email,
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_admin=is_first_user,
        security_level=5 if is_first_user else 1,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return issue_tokens(db, user)


def authenticate_user(db: Session, email: str, password: str) -> User:
    user = get_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    return user


def issue_tokens(db: Session, user: User) -> AuthResponse:
    settings = get_settings()
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token()
    refresh_model = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        expires_at=utc_now() + timedelta(days=settings.jwt_refresh_token_expire_days),
    )
    db.add(refresh_model)
    db.commit()
    db.refresh(user)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=user,
    )


def refresh_tokens(db: Session, refresh_token: str) -> AuthResponse:
    token_hash = hash_refresh_token(refresh_token)
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if not token or token.revoked_at is not None or _as_utc(token.expires_at) <= utc_now():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user = db.get(User, token.user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    token.revoked_at = utc_now()
    db.add(token)
    db.commit()
    return issue_tokens(db, user)


def revoke_refresh_token(db: Session, refresh_token: str) -> None:
    token_hash = hash_refresh_token(refresh_token)
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if token and token.revoked_at is None:
        token.revoked_at = utc_now()
        db.add(token)
        db.commit()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
