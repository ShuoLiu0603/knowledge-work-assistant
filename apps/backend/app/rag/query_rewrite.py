from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.llm.provider import LlmMessage, create_chat_model, get_llm_provider, to_langchain_messages

MAX_REWRITTEN_QUERY_CHARS = 300
MAX_SUB_QUERY_CHARS = 300
MAX_SUB_QUERIES = 3

POLITE_PREFIXES = (
    "请问",
    "请帮我",
    "帮我",
    "能否",
    "可以",
    "请",
    "please",
    "could you",
    "can you",
)


@dataclass(frozen=True)
class QueryRewritePlan:
    rewritten_query: str
    sub_questions: list[str]


class QueryRewriteOutput(BaseModel):
    rewritten_query: str = Field(default="", validation_alias=AliasChoices("rewritten_query", "query"))
    sub_questions: list[str] = Field(default_factory=list)

    @field_validator("rewritten_query", mode="before")
    @classmethod
    def normalize_rewritten_query(cls, value: object) -> str:
        return clean_text(value, MAX_REWRITTEN_QUERY_CHARS)

    @field_validator("sub_questions", mode="before")
    @classmethod
    def normalize_sub_questions(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned = [clean_text(item, MAX_SUB_QUERY_CHARS) for item in value]
        return [item for item in cleaned if item][:MAX_SUB_QUERIES]


def rewrite_query(question: str) -> QueryRewritePlan:
    try:
        output = structured_rewrite_with_langchain(question)
        return output_to_plan(output, question)
    except Exception:
        pass

    try:
        raw = get_llm_provider().complete(build_query_rewrite_messages(question), temperature=0)
    except Exception:
        return fallback_query_plan(question)
    return parse_query_rewrite(raw, question)


def structured_rewrite_with_langchain(question: str) -> QueryRewriteOutput:
    chat = create_chat_model(temperature=0, streaming=False)
    structured_chat = chat.with_structured_output(QueryRewriteOutput)
    result = structured_chat.invoke(to_langchain_messages(build_query_rewrite_messages(question)))
    return coerce_query_rewrite_output(result)


def coerce_query_rewrite_output(result: object) -> QueryRewriteOutput:
    if isinstance(result, QueryRewriteOutput):
        return result
    if isinstance(result, dict):
        return QueryRewriteOutput.model_validate(result)
    if isinstance(result, BaseModel):
        return QueryRewriteOutput.model_validate(result.model_dump())
    raise TypeError("Unexpected structured query rewrite output")


def build_query_rewrite_messages(question: str) -> list[LlmMessage]:
    system_prompt = (
        "You rewrite user questions for enterprise RAG retrieval. Return only a JSON object with "
        'two fields: "rewritten_query" and "sub_questions". '
        "rewritten_query must be a concise standalone search query. "
        "sub_questions must contain at most three independent search queries only when the user asks "
        "multiple things. Preserve names, identifiers, numbers, and the user's language. "
        "Do not answer the question and do not invent facts."
    )
    return [
        LlmMessage("system", system_prompt),
        LlmMessage("user", question),
    ]


def parse_query_rewrite(raw: str, original_query: str) -> QueryRewritePlan:
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict):
        return fallback_query_plan(original_query)
    return output_to_plan(QueryRewriteOutput.model_validate(parsed), original_query)


def output_to_plan(output: QueryRewriteOutput, original_query: str) -> QueryRewritePlan:
    rewritten_query = normalize_query(output.rewritten_query) or normalize_query(original_query)
    sub_questions = clean_sub_questions(output.sub_questions, original_query, rewritten_query)
    return QueryRewritePlan(rewritten_query=rewritten_query, sub_questions=sub_questions)


def parse_json_object(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def fallback_query_plan(question: str) -> QueryRewritePlan:
    return QueryRewritePlan(rewritten_query=normalize_query(question), sub_questions=[])


def normalize_query(question: str) -> str:
    normalized = normalize_whitespace(question)
    lowered = normalized.lower()
    for prefix in POLITE_PREFIXES:
        if lowered.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    return normalized.rstrip(" ?？。.!！").strip() or normalize_whitespace(question)


def normalize_whitespace(text: str) -> str:
    return " ".join(text.strip().split())


def clean_sub_questions(sub_questions: list[str], original_query: str, rewritten_query: str) -> list[str]:
    excluded = {normalize_whitespace(original_query), normalize_whitespace(rewritten_query)}
    cleaned = []
    for sub_question in sub_questions:
        value = normalize_query(sub_question)
        if len(value) < 2 or value in excluded:
            continue
        cleaned.append(value)
    return dedupe_preserve_order(cleaned)[:MAX_SUB_QUERIES]


def clean_text(value: object, max_chars: int) -> str:
    return normalize_whitespace(str(value or ""))[:max_chars]


def dedupe_preserve_order(values) -> list:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
