from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence

from app.core.state import ResearchState, RetrievalDocument
from app.es.retrieval import HybridRetriever

logger = logging.getLogger("ai_decision.agents.reflection")


@dataclass(frozen=True)
class ReflectionConfig:
    max_iterations: int = 3
    max_entities_per_iteration: int = 4
    min_new_docs_per_iteration: int = 1
    target_evidence_coverage: float = 0.75
    retrieval_size_per_entity: int = 3


class ReflectionAgent:
    """Expands evidence by iteratively searching entities related to initial hits."""

    def __init__(self, retriever: HybridRetriever):
        self.retriever = retriever

    def run(self, state: ResearchState) -> ResearchState:
        config = self._load_config(state)
        seen_doc_ids = {doc.id for doc in state.retrieval_results}
        seen_entities = set(state.related_entities)
        required_evidence = self._required_evidence(state)
        logger.info(
            "reflection_started scenario_id=%s initial_docs=%s required_evidence=%s max_iterations=%s",
            state.scenario_id,
            len(state.retrieval_results),
            ",".join(required_evidence),
            config.max_iterations,
        )

        for iteration in range(1, config.max_iterations + 1):
            candidates = self._rank_entities(state.retrieval_results, seen_entities)
            selected = candidates[: config.max_entities_per_iteration]
            coverage_before = self._evidence_coverage(state.retrieval_results, required_evidence)
            logger.info("reflection_iteration_started scenario_id=%s iteration=%s candidates=%s selected=%s", state.scenario_id, iteration, len(candidates), selected)

            if not selected:
                self._append_trace(state, iteration, [], 0, "no_new_related_entities")
                logger.info("reflection_stopped scenario_id=%s iteration=%s reason=no_new_related_entities", state.scenario_id, iteration)
                break

            new_docs: List[RetrievalDocument] = []
            for entity in selected:
                query = self._build_related_query(state, entity)
                try:
                    docs = self.retriever.search(query, size=config.retrieval_size_per_entity)
                except Exception as exc:
                    state.errors.append(f"reflection_retrieval_failed:{entity}:{exc}")
                    logger.exception("reflection_entity_retrieval_failed scenario_id=%s iteration=%s entity=%s", state.scenario_id, iteration, entity)
                    continue

                for doc in docs:
                    if doc.id not in seen_doc_ids:
                        seen_doc_ids.add(doc.id)
                        new_docs.append(doc)

            seen_entities.update(selected)
            state.related_entities = sorted(seen_entities)
            state.reflection_iterations = iteration

            if new_docs:
                state.retrieval_results = self._merge_documents(state.retrieval_results, new_docs)

            coverage_after = self._evidence_coverage(state.retrieval_results, required_evidence)
            state.evidence_coverage = coverage_after
            stop_reason = self._stop_reason(
                config=config,
                new_doc_count=len(new_docs),
                coverage=coverage_after,
                required_evidence=required_evidence,
            )
            self._append_trace(state, iteration, selected, len(new_docs), stop_reason or "continue")
            logger.info(
                "reflection_iteration_completed scenario_id=%s iteration=%s selected=%s new_docs=%s stop_reason=%s coverage=%s",
                state.scenario_id,
                iteration,
                selected,
                len(new_docs),
                stop_reason or "continue",
                coverage_after,
            )

            if stop_reason:
                break

            if coverage_after == coverage_before and len(new_docs) < config.min_new_docs_per_iteration:
                logger.info("reflection_stopped scenario_id=%s iteration=%s reason=no_coverage_gain", state.scenario_id, iteration)
                break

        if not state.evidence_coverage:
            state.evidence_coverage = self._evidence_coverage(state.retrieval_results, required_evidence)
        logger.info(
            "reflection_finished scenario_id=%s iterations=%s related_entities=%s total_docs=%s",
            state.scenario_id,
            state.reflection_iterations,
            len(state.related_entities),
            len(state.retrieval_results),
        )
        return state

    def _load_config(self, state: ResearchState) -> ReflectionConfig:
        raw = (state.scenario_config or {}).get("reflection") or {}
        return ReflectionConfig(
            max_iterations=int(raw.get("max_iterations", 3)),
            max_entities_per_iteration=int(raw.get("max_entities_per_iteration", 4)),
            min_new_docs_per_iteration=int(raw.get("min_new_docs_per_iteration", 1)),
            target_evidence_coverage=float(raw.get("target_evidence_coverage", 0.75)),
            retrieval_size_per_entity=int(raw.get("retrieval_size_per_entity", 3)),
        )

    def _required_evidence(self, state: ResearchState) -> List[str]:
        scenario = state.scenario_config or {}
        skill = (scenario.get("skills") or {}).get(state.chosen_skill or state.route or "", {})
        return list(skill.get("required_evidence") or [])

    def _rank_entities(self, docs: Sequence[RetrievalDocument], seen_entities: set[str]) -> List[str]:
        scores: Dict[str, int] = {}
        for doc in docs[:12]:
            for entity in self._extract_entities(doc):
                if entity in seen_entities:
                    continue
                scores[entity] = scores.get(entity, 0) + 1
        return [entity for entity, _ in sorted(scores.items(), key=lambda item: (-item[1], len(item[0]), item[0]))]

    def _extract_entities(self, doc: RetrievalDocument) -> List[str]:
        entities: List[str] = []
        metadata_keys = (
            "company",
            "brand",
            "product",
            "product_name",
            "competitor",
            "supplier",
            "channel",
            "industry",
        )
        for key in metadata_keys:
            value = doc.metadata.get(key)
            if isinstance(value, str):
                entities.extend(self._split_entity_value(value))
            elif isinstance(value, list):
                entities.extend(str(item) for item in value if item)

        text = f"{doc.title or ''} {doc.text[:1200]}"
        patterns = [
            r"[\u4e00-\u9fa5A-Za-z0-9]{2,24}(?:股份|集团|公司|药业|医药|制药|健康|品牌)",
            r"[\u4e00-\u9fa5A-Za-z0-9]{2,24}(?:片|丸|胶囊|颗粒|饮|膏|液|剂|新品|产品)",
            r"(?:竞品|竞争对手|供应商|渠道|平台|医院|药店|电商)[：:为是包括]*([\u4e00-\u9fa5A-Za-z0-9、，,]{2,80})",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, text):
                entities.extend(self._split_entity_value(match))

        return self._normalize_entities(entities)

    def _split_entity_value(self, value: str) -> List[str]:
        return [item.strip() for item in re.split(r"[、,，;；/|]", value) if item.strip()]

    def _normalize_entities(self, entities: Iterable[str]) -> List[str]:
        stopwords = {
            "市场",
            "新品",
            "产品",
            "公司",
            "集团",
            "渠道",
            "竞争对手",
            "供应商",
            "行业",
            "最近5年",
        }
        normalized: List[str] = []
        for entity in entities:
            item = re.sub(r"\s+", "", entity).strip("：:，,。；;（）()[]【】")
            if len(item) < 2 or len(item) > 30 or item in stopwords:
                continue
            if item not in normalized:
                normalized.append(item)
        return normalized

    def _build_related_query(self, state: ResearchState, entity: str) -> str:
        return f"{state.query} {entity} 关联 实体 竞品 供应商 渠道 新品 表现 市场反馈"

    def _merge_documents(
        self,
        base_docs: List[RetrievalDocument],
        new_docs: List[RetrievalDocument],
        limit: int = 24,
    ) -> List[RetrievalDocument]:
        merged: Dict[str, RetrievalDocument] = {doc.id: doc for doc in base_docs}
        for doc in new_docs:
            merged.setdefault(doc.id, doc)
        return sorted(merged.values(), key=lambda doc: doc.score, reverse=True)[:limit]

    def _evidence_coverage(self, docs: Sequence[RetrievalDocument], required_evidence: Sequence[str]) -> Dict[str, bool]:
        coverage = {item: False for item in required_evidence}
        if not coverage:
            return coverage

        text = "\n".join(f"{doc.title or ''}\n{doc.text}\n{doc.metadata}" for doc in docs).lower()
        keyword_map = {
            "product_launch": ["上市", "新品", "推出", "发布", "获批", "首发"],
            "market_feedback": ["反馈", "反响", "口碑", "评价", "认可", "消费者", "患者"],
            "sales_or_growth_signal": ["销售", "收入", "增长", "销量", "放量", "增速", "份额"],
            "channel_signal": ["渠道", "药店", "医院", "电商", "线上", "线下", "经销"],
            "competitor_signal": ["竞品", "竞争", "对手", "替代", "同类"],
            "supply_signal": ["供应", "产能", "原料", "采购", "供应商"],
        }
        for evidence in coverage:
            keywords = keyword_map.get(evidence, [evidence])
            coverage[evidence] = any(keyword.lower() in text for keyword in keywords)
        return coverage

    def _stop_reason(
        self,
        config: ReflectionConfig,
        new_doc_count: int,
        coverage: Dict[str, bool],
        required_evidence: Sequence[str],
    ) -> str | None:
        if new_doc_count < config.min_new_docs_per_iteration:
            return "low_marginal_new_docs"
        if required_evidence:
            ratio = sum(1 for value in coverage.values() if value) / len(required_evidence)
            if ratio >= config.target_evidence_coverage:
                return "target_evidence_coverage_reached"
        return None

    def _append_trace(
        self,
        state: ResearchState,
        iteration: int,
        entities: List[str],
        new_doc_count: int,
        stop_reason: str,
    ) -> None:
        state.reflection_trace.append(
            {
                "iteration": iteration,
                "entities": entities,
                "new_doc_count": new_doc_count,
                "stop_reason": stop_reason,
            }
        )
