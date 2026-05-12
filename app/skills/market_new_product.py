from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from app.core.llm import call_llm_json
from app.core.state import ResearchState, RetrievalDocument
from app.skills.citation_utils import citation_details, citation_label, citation_labels

logger = logging.getLogger("ai_decision.skills.market_new_product")


def market_new_product_skill(state: ResearchState) -> ResearchState:
    logger.info("market_new_product_skill_started scenario_id=%s retrieval_docs=%s", state.scenario_id, len(state.retrieval_results))
    state.skills_used.append("market_new_product_skill")
    state.modules_used.extend(["G1", "G4", "G10"])

    if not state.retrieval_results:
        state.answer = "未检索到相关数据，请检查 ES 索引、字段映射或尝试补充公司/产品关键词。"
        state.citations = []
        state.confidence = 0.25
        state.follow_up_questions = ["是否需要扩大到同类公司新品表现进行对比？"]
        logger.warning("market_new_product_no_retrieval_results scenario_id=%s", state.scenario_id)
        return state

    company = _extract_company(state.query)
    products = _extract_product_candidates(state.retrieval_results)
    timeline = _extract_timeline(state.retrieval_results)
    documents = [doc.model_dump() for doc in state.retrieval_results]
    llm_result = call_llm_json(
        "",
        params={
            "task": "market_new_product",
            "query": state.query,
            "company": company,
            "products": products,
            "timeline": timeline,
            "documents": documents,
        },
    )

    citations = citation_labels(state.retrieval_results[:5])
    details = citation_details(state.retrieval_results[:10])
    sections = _build_answer_sections(
        company=company,
        products=products,
        timeline=timeline,
        evidence_docs=state.retrieval_results[:8],
        llm_text=llm_result.get("text", ""),
    )
    answer = _compose_answer(
        query=state.query,
        company=company,
        products=products,
        timeline=timeline,
        evidence_docs=state.retrieval_results[:5],
        llm_text=llm_result.get("text", ""),
    )

    state.answer = answer
    state.answer_sections = sections
    state.citations = citations
    state.citation_details = details
    state.confidence = _confidence_with_reflection(
        base_confidence=float(llm_result.get("metadata", {}).get("confidence", 0.55)),
        evidence_coverage=state.evidence_coverage,
        reflection_iterations=state.reflection_iterations,
    )
    state.follow_up_questions = [
        f"{company}新品表现与主要竞品相比有哪些差异？",
        f"{company}新品增长主要来自渠道、价格还是产品定位？",
        "是否需要继续拆成年度新品清单和销量/声量趋势表？",
    ]
    state.skill_output = {
        "company": company,
        "product_candidates": products,
        "timeline": timeline,
        "citations": citations,
        "sections": sections,
    }
    logger.info(
        "market_new_product_skill_completed scenario_id=%s company=%s products=%s timeline_events=%s sections=%s citations=%s confidence=%.2f",
        state.scenario_id,
        company,
        len(products),
        len(timeline),
        len(sections),
        len(citations),
        state.confidence or 0.0,
    )
    return state


def _extract_company(query: str) -> str:
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,20})(?:最近|近|过去|的|市场|新品)", query)
    return match.group(1) if match else "目标公司"


def _extract_product_candidates(docs: List[RetrievalDocument]) -> List[str]:
    candidates: List[str] = []
    for doc in docs:
        for key in ("product", "product_name", "sku", "brand"):
            value = doc.metadata.get(key)
            if isinstance(value, str) and value not in candidates:
                candidates.append(value)

        for token in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,24}(?:片|丸|胶囊|颗粒|饮|膏|液|剂|新品|产品)", doc.text):
            if token not in candidates:
                candidates.append(token)
            if len(candidates) >= 8:
                return candidates
    return candidates[:8]


def _extract_timeline(docs: List[RetrievalDocument]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for doc in docs:
        dates = re.findall(r"(20[2-9][0-9](?:[-/.年](?:0?[1-9]|1[0-2])(?:[-/.月](?:0?[1-9]|[12][0-9]|3[01])日?)?)?)", doc.text)
        for date in dates[:3]:
            events.append({"date": date.replace("年", "-").replace("月", "-").replace("日", ""), "summary": doc.text[:180], "citation": citation_label(doc, len(events) + 1)})
    return events[:10]


def _compose_answer(
    query: str,
    company: str,
    products: List[str],
    timeline: List[Dict[str, Any]],
    evidence_docs: List[RetrievalDocument],
    llm_text: str,
) -> str:
    product_text = "、".join(products[:6]) if products else "检索结果尚未稳定抽取出明确新品名称"
    timeline_lines = []
    for item in timeline[:5]:
        timeline_lines.append(f"- {item['date']}：{item['summary']} [{item['citation']}]")
    if not timeline_lines:
        timeline_lines.append("- 检索结果中暂未抽取到明确日期，需要后续接入更强的信息抽取或补充结构化新品字段。")

    evidence_lines = [f"- [{idx}] {doc.text[:220].strip()} [{citation_label(doc, idx)}]" for idx, doc in enumerate(evidence_docs, start=1)]
    reflection_text = ""
    if evidence_docs:
        reflection_text = "系统已启用关联实体反思检索，会围绕公司、产品、竞品、渠道和供应链实体补充证据。"

    return (
        f"针对问题“{query}”，当前 P0 链路已按“新品清单 - 最近5年时序 - 表现总结 - 原因解释”的结构完成检索增强分析。\n\n"
        f"**新品清单**\n{company}相关新品候选包括：{product_text}。\n\n"
        f"**最近5年时序**\n" + "\n".join(timeline_lines) + "\n\n"
        f"**表现总结**\n{llm_text}\n\n"
        "**原因解释**\n"
        "现阶段判断主要来自 ES 文档中的产品、上市、渠道、销售/声量和市场反馈证据。"
        f"{reflection_text}"
        "若后续接入真实 LLM、销量数据库或电商数据，可进一步拆解为年度销售走势、渠道贡献、用户反馈、竞品替代和生命周期阶段。\n\n"
        "**关键引用**\n" + "\n".join(evidence_lines)
    )


def _build_answer_sections(
    company: str,
    products: List[str],
    timeline: List[Dict[str, Any]],
    evidence_docs: List[RetrievalDocument],
    llm_text: str,
) -> List[Dict[str, Any]]:
    top_citations = citation_labels(evidence_docs[:5])
    sections = [
        {
            "id": "product_inventory",
            "title": "新品清单",
            "summary": f"{company}相关新品候选已从语料中抽取，第一版以文档证据为准，后续可接结构化新品库校正。",
            "columns": ["类别", "发现", "引用"],
            "rows": [
                {
                    "类别": "新品候选",
                    "发现": "、".join(products[:8]) if products else "暂未抽取到稳定新品名称",
                    "引用": "、".join(top_citations[:3]),
                }
            ],
            "citations": top_citations[:3],
        },
        {
            "id": "five_year_timeline",
            "title": "最近5年时序",
            "summary": "按语料中的日期线索整理上市、渠道、销售和市场反馈事件。",
            "columns": ["时间", "事件", "引用"],
            "rows": [
                {"时间": item.get("date", "-"), "事件": item.get("summary", "-"), "引用": item.get("citation", "-")}
                for item in timeline[:6]
            ]
            or [{"时间": "-", "事件": "暂未抽取到明确日期事件", "引用": "、".join(top_citations[:2])}],
            "citations": list(dict.fromkeys([item.get("citation", "") for item in timeline if item.get("citation")]))[:5] or top_citations[:2],
        },
        {
            "id": "performance",
            "title": "表现判断",
            "summary": "综合新品、渠道、销售增长和市场反馈证据，形成阶段性表现判断。",
            "columns": ["维度", "判断", "引用"],
            "rows": [
                {"维度": "整体表现", "判断": llm_text[:260] or "当前证据不足，需要补充销售或声量数据。", "引用": "、".join(top_citations[:3])},
                {"维度": "证据强度", "判断": "若 ES 语料覆盖销售、渠道、反馈三类信号，则判断可信度更高。", "引用": "、".join(top_citations[:4])},
            ],
            "citations": top_citations[:4],
        },
        {
            "id": "causal_explanation",
            "title": "原因解释",
            "summary": "将表现变化拆成产品供给、渠道触达、市场反馈和竞争替代四类原因。",
            "columns": ["原因类别", "解释", "引用"],
            "rows": [
                {"原因类别": "产品供给", "解释": "新品上市节奏和产品形态决定初期可见度。", "引用": "、".join(top_citations[:2])},
                {"原因类别": "渠道触达", "解释": "线上、电商、药店、医院等渠道信号影响放量速度。", "引用": "、".join(top_citations[:3])},
                {"原因类别": "市场反馈", "解释": "用户、患者或消费者反馈决定新品能否持续复购和扩散。", "引用": "、".join(top_citations[:4])},
            ],
            "citations": top_citations[:4],
        },
        {
            "id": "effective_summary",
            "title": "有效总结",
            "summary": "本轮输出适合作为初筛结论：先判断新品表现方向，再定位需要补充的数据缺口。",
            "columns": ["结论项", "内容", "下一步"],
            "rows": [
                {"结论项": "可用结论", "内容": "已完成证据驱动的新品表现初判。", "下一步": "接入真实 GPT-5.5 后生成更强归因和经营建议。"},
                {"结论项": "主要缺口", "内容": "若 ES 中缺少销量、份额或渠道数据，表现判断会偏定性。", "下一步": "补充电商、销售、公告和研报结构化数据。"},
            ],
            "citations": top_citations[:5],
        },
    ]
    return sections
def _confidence_with_reflection(
    base_confidence: float,
    evidence_coverage: Dict[str, bool],
    reflection_iterations: int,
) -> float:
    if not evidence_coverage:
        return base_confidence
    coverage_ratio = sum(1 for value in evidence_coverage.values() if value) / len(evidence_coverage)
    reflection_bonus = min(reflection_iterations, 3) * 0.03
    return min(0.88, round(base_confidence * 0.75 + coverage_ratio * 0.2 + reflection_bonus, 2))
