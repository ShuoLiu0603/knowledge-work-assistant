from __future__ import annotations

import json
from datetime import datetime, timezone

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.conversation import Message


def get_redis_client():
    settings = get_settings()
    try:
        return redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return None


def append_short_term_memory(user_id: str, conversation_id: str | None, role: str, content: str) -> None:
    if not conversation_id or not content.strip():
        return
    client = get_redis_client()
    if client is None:
        return
    settings = get_settings()
    key = short_memory_key(user_id, conversation_id)
    payload = json.dumps(
        {
            "role": role,
            "content": content.strip()[:2000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
    )
    try:
        client.lpush(key, payload)
        client.ltrim(key, 0, settings.short_memory_max_messages - 1)
        client.expire(key, 60 * 60 * 24)
    except Exception:
        return


def get_short_term_memory(user_id: str, conversation_id: str | None) -> list[dict]:
    if not conversation_id:
        return []
    client = get_redis_client()
    if client is None:
        return []
    key = short_memory_key(user_id, conversation_id)
    try:
        rows = client.lrange(key, 0, -1)
    except Exception:
        return []
    messages = []
    for row in reversed(rows):
        try:
            messages.append(json.loads(row))
        except json.JSONDecodeError:
            continue
    return messages


def get_recent_db_messages(db: Session, conversation_id: str | None, limit: int = 8) -> list[dict]:
    if not conversation_id:
        return []
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at.isoformat(),
        }
        for message in reversed(rows)
    ]


def short_memory_key(user_id: str, conversation_id: str) -> str:
    return f"memory:short:{user_id}:{conversation_id}"
