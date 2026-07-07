from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.knowledge_base_service import ensure_kb_access


def ensure_viewer_access(db: Session, user_id: str, knowledge_base_id: str) -> None:
    ensure_kb_access(db, user_id, knowledge_base_id, required_role="viewer")
