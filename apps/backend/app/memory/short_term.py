from __future__ import annotations

import json
from datetime import datetime, timezone

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.conversation import Message

_SETTINGS = get_settings()
SHORT_MEMORY_TTL_SECONDS = _SETTINGS.short_memory_ttl_seconds
SHORT_MEMORY_CONTENT_MAX_CHARS = _SETTINGS.short_memory_content_max_chars
SHORT_MEMORY_ROLES = {"user", "assistant"}


def get_redis_client():
    settings = get_settings()
    try:
        return redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
    except Exception:
        return None


def append_short_term_memory(user_id: str, conversation_id: str | None, role: str, content: str) -> None:
    normalized_role = role.strip().lower()
    normalized_content = content.strip()
    if not user_id.strip() or not conversation_id or normalized_role not in SHORT_MEMORY_ROLES or not normalized_content:
        return
    client = get_redis_client()
    if client is None:
        return
    settings = get_settings()
    key = short_memory_key(user_id, conversation_id)
    payload = json.dumps(
        {
            "role": normalized_role,
            "content": normalized_content[:SHORT_MEMORY_CONTENT_MAX_CHARS],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
    )
    try:
        with client.pipeline(transaction=True) as pipeline:
            pipeline.lpush(key, payload)
            pipeline.ltrim(key, 0, max(1, settings.short_memory_max_messages) - 1)
            pipeline.expire(key, SHORT_MEMORY_TTL_SECONDS)
            pipeline.execute()
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
    if not isinstance(rows, (list, tuple)):
        return []
    messages = []
    for row in reversed(rows):
        message = parse_short_term_memory_row(row)
        if message is not None:
            messages.append(message)
    return messages


def clear_short_term_memory(user_id: str, conversation_id: str | None) -> bool:
    if not user_id.strip() or not conversation_id:
        return False
    client = get_redis_client()
    if client is None:
        return False
    try:
        return bool(client.delete(short_memory_key(user_id, conversation_id)))
    except Exception:
        return False


def parse_short_term_memory_row(row: object) -> dict | None:
    if isinstance(row, bytes):
        try:
            row = row.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(row, str):
        return None
    try:
        payload = json.loads(row)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    role = payload.get("role")
    content = payload.get("content")
    created_at = payload.get("created_at")
    if role not in SHORT_MEMORY_ROLES or not isinstance(content, str) or not content.strip():
        return None
    if not isinstance(created_at, str) or not is_iso_datetime(created_at):
        return None
    return {
        "role": role,
        "content": content.strip()[:SHORT_MEMORY_CONTENT_MAX_CHARS],
        "created_at": created_at,
    }


def is_iso_datetime(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def get_recent_db_messages(db: Session, conversation_id: str | None, limit: int | None = None) -> list[dict]:
    if not conversation_id:
        return []
    limit = limit or get_settings().short_memory_max_messages
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
            "memory_enabled": message.memory_enabled,
            "created_at": message.created_at.isoformat(),
        }
        for message in reversed(rows)
    ]


def short_memory_key(user_id: str, conversation_id: str) -> str:
    return f"memory:short:{user_id}:{conversation_id}"
