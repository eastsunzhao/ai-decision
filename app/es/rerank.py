from __future__ import annotations

import logging
import re
from typing import Iterable, List

from app.core.state import RetrievalDocument

logger = logging.getLogger("ai_decision.es.rerank")


class HeuristicReranker:
    """Lightweight reranker for the first production pass.

    It starts from RRF, then boosts chunks that contain the rewritten query terms
    in title, metadata and body. This is deterministic, cheap, and easy to
    inspect before a cross-encoder reranker is introduced.
    """

    def rerank(self, documents: List[RetrievalDocument], query: str, top_k: int) -> List[RetrievalDocument]:
        if not documents:
            return []

        terms = _query_terms(query)
        max_rrf = max((doc.score for doc in documents), default=1.0) or 1.0
        reranked = []
        for doc in documents:
            base_score = doc.score / max_rrf
            title_score = _term_coverage(terms, doc.title or "")
            text_score = _term_coverage(terms, doc.text)
            metadata_text = " ".join(str(value) for value in doc.metadata.values() if value is not None)
            metadata_score = _term_coverage(terms, metadata_text)
            exact_bonus = _exact_bonus(query, doc)
            rerank_score = (
                0.20 * base_score
                + 0.30 * title_score
                + 0.30 * text_score
                + 0.10 * metadata_score
                + 0.10 * exact_bonus
            )
            enriched = doc.model_copy(deep=True)
            enriched.metadata["rrf_score"] = round(doc.score, 6)
            enriched.metadata["rerank_score"] = round(rerank_score, 6)
            enriched.metadata["rerank_terms"] = terms[:20]
            enriched.score = rerank_score
            reranked.append(enriched)

        reranked.sort(key=lambda item: item.score, reverse=True)
        logger.info(
            "rerank_completed input=%s returned=%s terms=%s top_score=%.4f",
            len(documents),
            min(top_k, len(reranked)),
            len(terms),
            reranked[0].score if reranked else 0.0,
        )
        return reranked[:top_k]


def _query_terms(query: str) -> List[str]:
    raw_terms = []
    for part in re.split(r"[\s,，。；;:：、/|()\[\]{}<>《》\"'“”‘’]+", query or ""):
        part = part.strip()
        if part:
            raw_terms.append(part)
    raw_terms.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]{1,}|[\u4e00-\u9fff]{2,}", query or ""))

    stopwords = {
        "请",
        "进行",
        "开展",
        "系统",
        "系统性",
        "分析",
        "研究",
        "产品",
        "技术",
        "领域",
        "最近",
        "如何",
        "什么",
        "哪些",
    }
    terms = []
    seen = set()
    for term in raw_terms:
        normalized = term.strip()
        if len(normalized) < 2 or normalized in stopwords or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
    return terms[:40]


def _term_coverage(terms: Iterable[str], text: str) -> float:
    haystack = (text or "").lower()
    if not haystack:
        return 0.0
    term_list = list(terms)
    if not term_list:
        return 0.0
    hits = sum(1 for term in term_list if term.lower() in haystack)
    return min(hits / max(len(term_list), 1), 1.0)


def _exact_bonus(query: str, doc: RetrievalDocument) -> float:
    compact_query = re.sub(r"\s+", "", query or "")
    if not compact_query or len(compact_query) < 4:
        return 0.0
    title = re.sub(r"\s+", "", doc.title or "")
    text = re.sub(r"\s+", "", doc.text or "")
    if compact_query in title:
        return 1.0
    if compact_query in text:
        return 0.6
    return 0.0
