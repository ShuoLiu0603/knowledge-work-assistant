from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.agents.state import AgentGraphState, add_trace
from app.llm.provider import get_llm_provider
from app.services.llm_log_service import create_llm_call_log
from app.services.memory_service import is_memory_recall_query

SUMMARY_RE = re.compile(r"(总结|概括|摘要|归纳|summary|summarize|tl;dr|tldr)", re.IGNORECASE)
WRITING_RE = re.compile(
    r"(写|撰写|起草|拟一份|生成一份|帮我.*(邮件|通知|报告|方案|文案|函)|"
    r"draft|write|compose|email|proposal|report)",
    re.IGNORECASE,
)
CHAT_RE = re.compile(r"^(你好|您好|嗨|hello|hi|hey|谢谢|感谢|thanks|thank you)[。！!.\s]*$", re.IGNORECASE)
ENTERPRISE_RE = re.compile(
    r"(知识库|文档|制度|政策|流程|规范|标准|报销|审批|合同|发票|权限|依据|根据|"
    r"policy|procedure|document|knowledge base|reimbursement|invoice|contract|approval)",
    re.IGNORECASE,
)


def route_intent(db: Session, state: AgentGraphState) -> AgentGraphState:
    provider = get_llm_provider()
    raw_intent = "rag"
    llm_log_id = None
    if hasattr(provider, "classify_intent_with_metadata"):
        classification = provider.classify_intent_with_metadata(state.input)
        raw_intent = classification.intent
        llm_log = create_llm_call_log(
            db,
            classification.completion,
            user_id=state.user_id,
            conversation_id=state.conversation_id,
            agent_name="supervisor",
        )
        llm_log_id = llm_log.id
        state.llm_log_ids.append(llm_log.id)
    else:
        raw_intent = provider.classify_intent(state.input)
    intent = normalize_intent(raw_intent, state.input)
    state.intent = intent
    add_trace(
        state,
        node="supervisor",
        action="route_intent_with_llm_provider",
        input_data={"input": state.input},
        output_data={
            "intent": intent,
            "raw_intent": raw_intent,
            "provider": provider.provider_name,
            "llm_log_id": llm_log_id,
        },
    )
    return state


def normalize_intent(intent: str, text: str) -> str:
    normalized_intent = normalize_raw_intent(intent)
    if normalized_intent == "writing" and WRITING_RE.search(text):
        return "writing"
    if normalized_intent == "summary" and SUMMARY_RE.search(text):
        return "summary"
    if is_memory_recall_query(text):
        return "memory"
    if normalized_intent == "memory" and not ENTERPRISE_RE.search(text):
        return "memory"
    if normalized_intent == "chat" and CHAT_RE.search(text):
        return "chat"
    return "rag"


def normalize_raw_intent(intent: str) -> str:
    normalized = intent.strip().lower()
    if "summary" in normalized:
        return "summary"
    if "writing" in normalized:
        return "writing"
    if "memory" in normalized:
        return "memory"
    if "chat" in normalized:
        return "chat"
    return "rag"
