"""persist conversation history provenance

Revision ID: 20260710_0017
Revises: 20260710_0016
Create Date: 2026-07-10
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa

revision = "20260710_0017"
down_revision = "20260710_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "searched_knowledge_base_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "history_provenance_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    bind = op.get_bind()
    conversations = sa.table(
        "conversations",
        sa.column("id", sa.String()),
        sa.column("knowledge_base_id", sa.String()),
        sa.column("searched_knowledge_base_ids", sa.JSON()),
        sa.column("history_provenance_complete", sa.Boolean()),
        sa.column("summary", sa.Text()),
        sa.column("summary_message_count", sa.Integer()),
    )
    agent_runs = sa.table(
        "agent_runs",
        sa.column("conversation_id", sa.String()),
        sa.column("knowledge_base_id", sa.String()),
        sa.column("state", sa.JSON()),
    )
    retrieval_logs = sa.table(
        "retrieval_logs",
        sa.column("conversation_id", sa.String()),
        sa.column("knowledge_base_id", sa.String()),
        sa.column("searched_knowledge_base_ids", sa.JSON()),
    )
    messages = sa.table(
        "messages",
        sa.column("conversation_id", sa.String()),
        sa.column("role", sa.String()),
        sa.column("citations", sa.JSON()),
    )

    for conversation in bind.execute(
        sa.select(conversations.c.id, conversations.c.knowledge_base_id)
    ).mappings():
        knowledge_base_ids: set[str] = set()
        has_operational_provenance = False
        if conversation["knowledge_base_id"]:
            knowledge_base_ids.add(conversation["knowledge_base_id"])
        run_rows = list(bind.execute(
            sa.select(agent_runs.c.knowledge_base_id, agent_runs.c.state).where(
                agent_runs.c.conversation_id == conversation["id"]
            )
        ).mappings())
        has_operational_provenance = bool(run_rows)
        for run in run_rows:
            if run["knowledge_base_id"]:
                knowledge_base_ids.add(run["knowledge_base_id"])
            state = normalized_json(run["state"])
            knowledge_base_ids.update(normalized_ids(state.get("searched_knowledge_base_ids")))
        log_rows = list(bind.execute(
            sa.select(
                retrieval_logs.c.knowledge_base_id,
                retrieval_logs.c.searched_knowledge_base_ids,
            ).where(retrieval_logs.c.conversation_id == conversation["id"])
        ).mappings())
        has_operational_provenance = has_operational_provenance or bool(log_rows)
        for log in log_rows:
            if log["knowledge_base_id"]:
                knowledge_base_ids.add(log["knowledge_base_id"])
            knowledge_base_ids.update(normalized_ids(log["searched_knowledge_base_ids"]))
        message_rows = list(bind.execute(
            sa.select(messages.c.role, messages.c.citations).where(
                messages.c.conversation_id == conversation["id"]
            )
        ).mappings())
        for message in message_rows:
            citations = normalized_list(message["citations"])
            knowledge_base_ids.update(
                citation.get("knowledge_base_id")
                for citation in citations
                if isinstance(citation, dict)
                and isinstance(citation.get("knowledge_base_id"), str)
                and citation.get("knowledge_base_id")
            )
        provenance_complete = bool(
            conversation["knowledge_base_id"]
            or not message_rows
            or has_operational_provenance
            or knowledge_base_ids
        )

        bind.execute(
            sa.update(conversations)
            .where(conversations.c.id == conversation["id"])
            .values(
                searched_knowledge_base_ids=sorted(knowledge_base_ids),
                history_provenance_complete=provenance_complete,
                summary=None,
                summary_message_count=0,
            )
        )


def downgrade() -> None:
    op.drop_column("conversations", "history_provenance_complete")
    op.drop_column("conversations", "searched_knowledge_base_ids")


def normalized_json(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalized_ids(value: object) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return set()
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def normalized_list(value: object) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []
