from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.core.security import hash_password
from app.db.base import Base
from app.db.models.user import User


@contextmanager
def isolated_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def create_user(session: Session, email: str, username: str = "Test User") -> User:
    user = User(
        email=email,
        username=username,
        hashed_password=hash_password("Password123!"),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
