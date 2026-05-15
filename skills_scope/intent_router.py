"""Lightweight turn-intent router used before scope strategy planning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
    should_reuse_context: bool
    should_search: bool
    reason: str
    slots: dict[str, Any]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _tokenize(text: str) -> set[str]:
    norm = _normalize(text)
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", norm)
    zh_tokens: set[str] = set()
    for chunk in zh_chunks:
        if len(chunk) <= 3:
            zh_tokens.add(chunk)
            continue
        for i in range(0, len(chunk) - 1):
            zh_tokens.add(chunk[i : i + 2])
    en_tokens = set(re.findall(r"[a-z0-9][a-z0-9._/-]{1,}", norm))
    return zh_tokens | en_tokens


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_affirmative_short(text: str) -> bool:
    marks = {
        "可以",
        "好的",
        "好",
        "行",
        "嗯",
        "嗯嗯",
        "ok",
        "okay",
        "yes",
        "y",
        "继续吧",
    }
    return len(text) <= 8 and text in marks


def _is_stop_intent(text: str) -> bool:
    return _contains_any(
        text,
        ("停止", "停下", "不用了", "取消", "/stop", "stop", "cancel", "别查了"),
    )


def _is_citation_request(text: str) -> bool:
    return _contains_any(text, ("引用", "引文", "来源", "出处", "链接", "cite", "citation", "source", "url"))


def _is_continue_like(text: str) -> bool:
    markers = (
        "继续",
        "展开",
        "细说",
        "详细",
        "上面",
        "刚才",
        "这个",
        "那个",
        "再说",
        "接着",
        "continue",
        "expand",
        "more detail",
        "based on that",
        "然后呢",
        "这个呢",
        "那",
    )
    return len(text) <= 80 and _contains_any(text, markers)


def _is_question_like(text: str) -> bool:
    if "?" in text or "？" in text:
        return True
    return _contains_any(text, ("什么", "怎么", "为何", "如何", "why", "what", "how", "when", "where"))


def _semantic_similarity(message: str, topic_key: str) -> float:
    msg_tokens = _tokenize(message)
    topic_tokens = _tokenize(topic_key)
    if not msg_tokens or not topic_tokens:
        return 0.0
    overlap = len(msg_tokens & topic_tokens)
    denom = max(len(msg_tokens), len(topic_tokens))
    return overlap / float(denom)


def classify_turn_intent(
    *,
    message: str,
    topic_key: str,
    has_successful_evidence: bool,
    pending_continue: bool,
) -> IntentResult:
    text = _normalize(message)
    if not text:
        return IntentResult(
            intent="other",
            confidence=0.55,
            should_reuse_context=False,
            should_search=True,
            reason="empty input fallback to search",
            slots={},
        )

    citation = _is_citation_request(text)
    if _is_stop_intent(text):
        return IntentResult(
            intent="stop",
            confidence=0.98,
            should_reuse_context=False,
            should_search=False,
            reason="explicit stop command",
            slots={"citation": citation},
        )

    if has_successful_evidence and pending_continue and (_is_affirmative_short(text) or _is_continue_like(text)):
        return IntentResult(
            intent="confirm_continue",
            confidence=0.94,
            should_reuse_context=True,
            should_search=False,
            reason="pending continue acknowledged",
            slots={"citation": citation},
        )

    if has_successful_evidence and (_is_continue_like(text) or _is_affirmative_short(text)):
        return IntentResult(
            intent="confirm_continue",
            confidence=0.8,
            should_reuse_context=True,
            should_search=False,
            reason="follow-up continuation intent",
            slots={"citation": citation},
        )

    similarity = _semantic_similarity(text, topic_key)
    if has_successful_evidence and similarity >= 0.34 and not _is_question_like(text):
        return IntentResult(
            intent="clarify",
            confidence=min(0.9, 0.65 + similarity / 2),
            should_reuse_context=True,
            should_search=False,
            reason="semantically close to current topic",
            slots={"citation": citation, "similarity": round(similarity, 3)},
        )

    if _is_question_like(text):
        return IntentResult(
            intent="new_query",
            confidence=0.82,
            should_reuse_context=False,
            should_search=True,
            reason="question-like utterance",
            slots={"citation": citation},
        )

    return IntentResult(
        intent="other",
        confidence=0.6,
        should_reuse_context=has_successful_evidence and similarity >= 0.2,
        should_search=not (has_successful_evidence and similarity >= 0.2),
        reason="default intent routing",
        slots={"citation": citation, "similarity": round(similarity, 3)},
    )
