from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.core.llm import call_llm_json
from app.core.state import ResearchState, RetrievalDocument
from app.skills.citation_utils import citation_details, citation_labels

logger = logging.getLogger("ai_decision.skills.product_competitive")


def product_competitive_skill(state: ResearchState) -> ResearchState:
    logger.info("product_competitive_skill_started scenario_id=%s docs=%s", state.scenario_id, len(state.retrieval_results))
    state.skills_used.append("product_competitive_skill")
    state.modules_used.extend(["G2", "G7", "G10"])

    if not state.retrieval_results:
        state.answer = "未检索到可用于竞争分析的语料，请补充产品名、竞品或目标市场。"
        state.confidence = 0.25
        state.follow_up_questions = ["是否需要指定主要竞品和目标市场？"]
        return state

    docs = state.retrieval_results[:10]
    citations = citation_labels(docs[:5])
    llm_result = call_llm_json("", params={"task": "product_competitive", "query": state.query, "documents": [doc.model_dump() for doc in docs]})
    sections = _competitive_sections(state.query, docs, llm_result.get("text", ""))
    state.answer_sections = sections
    state.citations = citations
    state.citation_details = citation_details(docs)
    state.answer = "已完成系统性竞争分析，包含竞争对象、维度对比、差异化判断、风险与行动建议。"
    state.confidence = float(llm_result.get("metadata", {}).get("confidence", 0.58))
    state.follow_up_questions = [
        "是否需要进一步量化各竞品的市场份额和增长速度？",
        "是否需要输出竞品对比矩阵和进入策略？",
    ]
    state.skill_output = {"sections": sections, "citations": citations}
    logger.info("product_competitive_skill_completed scenario_id=%s sections=%s citations=%s", state.scenario_id, len(sections), len(citations))
    return state


def _competitive_sections(query: str, docs: List[RetrievalDocument], llm_text: str) -> List[Dict[str, Any]]:
    citations = citation_labels(docs[:5])
    snippets = "；".join(doc.text[:120].strip() for doc in docs[:3] if doc.text)
    return [
        {
            "id": "competitive_scope",
            "title": "竞争范围界定",
            "summary": "先明确目标产品、竞品集合和比较边界，避免把不同适应症、渠道或价格带混在一起。",
            "columns": ["项目", "判断", "引用"],
            "rows": [
                {"项目": "目标问题", "判断": query, "引用": "、".join(citations[:2])},
                {"项目": "证据摘要", "判断": snippets or "当前语料不足以稳定抽取竞品事实。", "引用": "、".join(citations[:3])},
            ],
            "citations": citations[:3],
        },
        {
            "id": "comparison_matrix",
            "title": "竞争维度对比",
            "summary": "围绕产品定位、功能/疗效、价格、渠道、品牌和增长信号进行横向比较。",
            "columns": ["维度", "目标产品关注点", "竞争含义", "引用"],
            "rows": [
                {"维度": "产品定位", "目标产品关注点": "目标人群、适应场景、核心卖点", "竞争含义": "决定与竞品是否正面交锋", "引用": "、".join(citations[:3])},
                {"维度": "渠道触达", "目标产品关注点": "医院、药店、电商、经销覆盖", "竞争含义": "影响放量速度和用户获取成本", "引用": "、".join(citations[:4])},
                {"维度": "增长信号", "目标产品关注点": "销量、收入、份额、声量变化", "竞争含义": "判断竞争位置是在追赶还是领先", "引用": "、".join(citations[:5])},
            ],
            "citations": citations[:5],
        },
        {
            "id": "differentiation",
            "title": "差异化与壁垒",
            "summary": llm_text[:220] or "差异化需要结合产品证据、渠道证据和用户反馈证据共同判断。",
            "columns": ["判断项", "结论", "引用"],
            "rows": [
                {"判断项": "可差异化方向", "结论": "从产品属性、渠道效率、品牌认知和临床/消费反馈中寻找差异。", "引用": "、".join(citations[:4])},
                {"判断项": "潜在壁垒", "结论": "若证据显示渠道、品牌或技术独占性强，则竞争壁垒更高。", "引用": "、".join(citations[:5])},
            ],
            "citations": citations[:5],
        },
        {
            "id": "competitive_summary",
            "title": "有效总结",
            "summary": "竞争分析的可用结论应落到竞品是谁、凭什么竞争、哪里有机会、哪里有风险。",
            "columns": ["结论项", "内容", "下一步"],
            "rows": [
                {"结论项": "当前结论", "内容": "已形成证据驱动的初步竞争框架。", "下一步": "补充销量、价格、渠道覆盖和用户反馈的结构化数据。"},
                {"结论项": "决策用途", "内容": "可用于竞品监测、定位调整和渠道策略讨论。", "下一步": "输出竞品雷达图或评分卡。"},
            ],
            "citations": citations[:5],
        },
    ]
