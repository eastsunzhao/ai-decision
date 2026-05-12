from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.core.llm import call_llm_json
from app.core.state import ResearchState, RetrievalDocument
from app.skills.citation_utils import citation_details, citation_labels

logger = logging.getLogger("ai_decision.skills.tech_trend")


def tech_trend_skill(state: ResearchState) -> ResearchState:
    logger.info("tech_trend_skill_started scenario_id=%s docs=%s", state.scenario_id, len(state.retrieval_results))
    state.skills_used.append("tech_trend_skill")
    state.modules_used.extend(["G4", "G12", "G96"])

    if not state.retrieval_results:
        state.answer = "未检索到可用于技术趋势研究的语料，请补充产品名、技术关键词或应用领域。"
        state.confidence = 0.25
        state.follow_up_questions = ["是否需要指定技术领域、时间范围或关键企业？"]
        return state

    docs = state.retrieval_results[:10]
    citations = citation_labels(docs[:5])
    llm_result = call_llm_json("", params={"task": "tech_trend", "query": state.query, "documents": [doc.model_dump() for doc in docs]})
    sections = _trend_sections(state.query, docs, llm_result.get("text", ""))
    state.answer_sections = sections
    state.citations = citations
    state.citation_details = citation_details(docs)
    state.answer = "已完成技术领域趋势研究，包含技术范围、演进阶段、关键方向、机会风险和有效总结。"
    state.confidence = float(llm_result.get("metadata", {}).get("confidence", 0.58))
    state.follow_up_questions = [
        "是否需要进一步梳理技术路线图和关键玩家？",
        "是否需要识别未来3年的技术机会和商业化风险？",
    ]
    state.skill_output = {"sections": sections, "citations": citations}
    logger.info("tech_trend_skill_completed scenario_id=%s sections=%s citations=%s", state.scenario_id, len(sections), len(citations))
    return state


def _trend_sections(query: str, docs: List[RetrievalDocument], llm_text: str) -> List[Dict[str, Any]]:
    citations = citation_labels(docs[:5])
    snippets = "；".join(doc.text[:120].strip() for doc in docs[:3] if doc.text)
    return [
        {
            "id": "tech_scope",
            "title": "技术领域界定",
            "summary": "先明确产品所处技术领域、上下游技术环节和应用场景。",
            "columns": ["项目", "判断", "引用"],
            "rows": [
                {"项目": "研究问题", "判断": query, "引用": "、".join(citations[:2])},
                {"项目": "证据摘要", "判断": snippets or "当前语料不足以稳定界定技术范围。", "引用": "、".join(citations[:3])},
            ],
            "citations": citations[:3],
        },
        {
            "id": "evolution_stage",
            "title": "技术演进阶段",
            "summary": "按时间、性能提升、产业采用和商业化成熟度判断技术阶段。",
            "columns": ["阶段", "关键特征", "判断要点", "引用"],
            "rows": [
                {"阶段": "早期探索", "关键特征": "概念验证、样机、专利或论文密集", "判断要点": "关注技术可行性", "引用": "、".join(citations[:2])},
                {"阶段": "产业导入", "关键特征": "试点、产线、示范应用增加", "判断要点": "关注成本与可靠性", "引用": "、".join(citations[:4])},
                {"阶段": "规模扩散", "关键特征": "标准化、供应链成熟、应用场景扩张", "判断要点": "关注竞争格局和替代风险", "引用": "、".join(citations[:5])},
            ],
            "citations": citations[:5],
        },
        {
            "id": "trend_drivers",
            "title": "趋势驱动因素",
            "summary": llm_text[:220] or "技术趋势通常由性能、成本、政策、供应链和下游需求共同驱动。",
            "columns": ["驱动因素", "趋势含义", "引用"],
            "rows": [
                {"驱动因素": "性能提升", "趋势含义": "决定技术能否替代旧方案或打开新场景。", "引用": "、".join(citations[:3])},
                {"驱动因素": "成本下降", "趋势含义": "决定商业化速度和规模化边界。", "引用": "、".join(citations[:4])},
                {"驱动因素": "应用需求", "趋势含义": "决定技术路线优先级和产业投资方向。", "引用": "、".join(citations[:5])},
            ],
            "citations": citations[:5],
        },
        {
            "id": "trend_summary",
            "title": "有效总结",
            "summary": "技术趋势研究应输出方向、阶段、关键玩家、机会窗口和风险约束。",
            "columns": ["结论项", "内容", "下一步"],
            "rows": [
                {"结论项": "当前结论", "内容": "已形成技术趋势的证据框架。", "下一步": "补充专利、论文、政策、产品发布和投融资数据。"},
                {"结论项": "决策用途", "内容": "可用于研发路线、投资主题和产品规划讨论。", "下一步": "输出技术路线图和时间轴。"},
            ],
            "citations": citations[:5],
        },
    ]
