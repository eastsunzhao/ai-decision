from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from app.agents.scenario import ScenarioManager
from app.core.embeddings import embed_query
from app.agents.reflection import ReflectionAgent
from app.core.config import settings
from app.core.llm import call_llm_json
from app.core.state import ResearchState
from app.es.retrieval import HybridRetriever
from app.prompts.query_rewrite import build_query_rewrite_prompt
from app.skills.market_new_product import market_new_product_skill
from app.skills.product_competitive import product_competitive_skill
from app.skills.tech_trend import tech_trend_skill

try:
    from langgraph.graph import END, StateGraph
except Exception:
    END = None
    StateGraph = None

logger = logging.getLogger("ai_decision.graph.workflow")


class AgentWorkflow:
    def __init__(self, scenario_manager: ScenarioManager):
        self.scenario_manager = scenario_manager
        self.retriever = HybridRetriever(settings.es_index, rrf_k=settings.retrieval_rrf_k)
        self.reflection_agent = ReflectionAgent(self.retriever)
        self.graph = self._build_graph()
        logger.info("workflow_initialized langgraph_enabled=%s es_index=%s", self.graph is not None, settings.es_index)

    def run(self, state: ResearchState) -> ResearchState:
        logger.info("workflow_run_started scenario_id=%s query_len=%s", state.scenario_id, len(state.query))
        if self.graph is None:
            result = self._run_sequential(state)
            logger.info("workflow_run_completed mode=sequential scenario_id=%s errors=%s", state.scenario_id, len(result.errors))
            return result
        result: Dict[str, Any] = self.graph.invoke(state.model_dump())
        final_state = ResearchState(**result)
        logger.info("workflow_run_completed mode=langgraph scenario_id=%s errors=%s", state.scenario_id, len(final_state.errors))
        return final_state

    def _build_graph(self):
        if StateGraph is None:
            logger.info("langgraph_not_installed fallback=sequential")
            return None

        graph = StateGraph(dict)
        graph.add_node("scenario_loader", self._dict_node(self.scenario_loader))
        graph.add_node("research_router", self._dict_node(self.research_router))
        graph.add_node("task_plan_builder", self._dict_node(self.task_plan_builder))
        graph.add_node("query_rewriter", self._dict_node(self.query_rewriter))
        graph.add_node("retrieval", self._dict_node(self.retrieval))
        graph.add_node("reflection_agent", self._dict_node(self.reflection_agent_node))
        graph.add_node("instruction_selector", self._dict_node(self.instruction_selector))
        graph.add_node("skill_executor", self._dict_node(self.skill_executor))
        graph.add_node("synthesis", self._dict_node(self.synthesis))
        graph.add_node("citation_checker", self._dict_node(self.citation_checker))

        graph.set_entry_point("scenario_loader")
        graph.add_edge("scenario_loader", "research_router")
        graph.add_edge("research_router", "task_plan_builder")
        graph.add_edge("task_plan_builder", "query_rewriter")
        graph.add_edge("query_rewriter", "retrieval")
        graph.add_edge("retrieval", "instruction_selector")
        graph.add_edge("instruction_selector", "reflection_agent")
        graph.add_edge("reflection_agent", "skill_executor")
        graph.add_edge("skill_executor", "synthesis")
        graph.add_edge("synthesis", "citation_checker")
        graph.add_edge("citation_checker", END)
        return graph.compile()

    def _dict_node(self, func):
        def wrapper(raw_state: Dict[str, Any]) -> Dict[str, Any]:
            state = ResearchState(**raw_state)
            return func(state).model_dump()

        return wrapper

    def _run_sequential(self, state: ResearchState) -> ResearchState:
        for node in (
            self.scenario_loader,
            self.research_router,
            self.task_plan_builder,
            self.query_rewriter,
            self.retrieval,
            self.instruction_selector,
            self.reflection_agent_node,
            self.skill_executor,
            self.synthesis,
            self.citation_checker,
        ):
            logger.info("workflow_node_started node=%s scenario_id=%s", node.__name__, state.scenario_id)
            state = node(state)
            logger.info("workflow_node_completed node=%s scenario_id=%s errors=%s", node.__name__, state.scenario_id, len(state.errors))
        return state

    def run_with_events(self, state: ResearchState):
        logger.info("workflow_stream_started scenario_id=%s query_len=%s", state.scenario_id, len(state.query))
        for node_name, node in (
            ("scenario_loader", self.scenario_loader),
            ("research_router", self.research_router),
            ("task_plan_builder", self.task_plan_builder),
            ("query_rewriter", self.query_rewriter),
            ("retrieval", self.retrieval),
            ("instruction_selector", self.instruction_selector),
            ("reflection_agent", self.reflection_agent_node),
            ("skill_executor", self.skill_executor),
            ("synthesis", self.synthesis),
            ("citation_checker", self.citation_checker),
        ):
            logger.info("workflow_stream_node_started node=%s scenario_id=%s", node_name, state.scenario_id)
            yield {"type": "step", "name": node_name, "status": "running", "message": self._step_message(node_name, "running")}
            state = node(state)
            logger.info("workflow_stream_node_completed node=%s scenario_id=%s errors=%s", node_name, state.scenario_id, len(state.errors))
            yield {
                "type": "step",
                "name": node_name,
                "status": "completed",
                "message": self._step_message(node_name, "completed"),
                "state": {
                    "task_plan": state.task_plan,
                    "execution_steps": state.execution_steps,
                    "reflection": {
                        "iterations": state.reflection_iterations,
                        "related_entities": state.related_entities,
                        "trace": state.reflection_trace,
                    },
                },
            }
        logger.info("workflow_stream_completed scenario_id=%s errors=%s", state.scenario_id, len(state.errors))
        yield {"type": "final", "data": state}

    def scenario_loader(self, state: ResearchState) -> ResearchState:
        state.scenario_config = self.scenario_manager.get_scenario(state.scenario_id) or {}
        scenario = state.scenario_config or {}
        directions = scenario.get("analysis_directions") or []
        data_sources = scenario.get("data_sources") or []
        workflow_order = scenario.get("workflow_order") or []
        self._append_method_trace(
            state,
            "scenario_loader",
            "方法追踪：加载场景=%s；开放分析方向=%s；数据源=%s；工作流=%s。"
            % (
                scenario.get("name", state.scenario_id),
                "、".join(item.get("name", item.get("direction_id", "")) for item in directions) or "-",
                "、".join(item.get("type", "-") for item in data_sources) or "-",
                " -> ".join(workflow_order) or "-",
            ),
        )
        logger.info("scenario_loaded scenario_id=%s found=%s", state.scenario_id, bool(state.scenario_config))
        return state

    def research_router(self, state: ResearchState) -> ResearchState:
        query = state.query.lower()
        scenario = state.scenario_config or {}
        for skill_id, skill in (scenario.get("skills") or {}).items():
            triggers = skill.get("triggers") or []
            matched = [str(trigger) for trigger in triggers if str(trigger).lower() in query]
            if matched:
                state.route = skill_id
                direction = self._direction_for_skill(scenario, skill_id)
                self._append_method_trace(
                    state,
                    "research_router",
                    "方法追踪：命中路由规则=trigger_match；触发词=%s；route=%s；analysis_direction=%s；methodology_config=%s。"
                    % (
                        "、".join(matched),
                        state.route,
                        direction.get("name", "-") if direction else "-",
                        direction.get("methodology_config", "-") if direction else "-",
                    ),
                )
                logger.info("research_route_selected scenario_id=%s route=%s trigger_match=true", state.scenario_id, state.route)
                return state
        state.route = scenario.get("default_route", "market_new_product_skill")
        direction = self._direction_for_skill(scenario, state.route)
        self._append_method_trace(
            state,
            "research_router",
            "方法追踪：命中路由规则=default_route；route=%s；analysis_direction=%s；methodology_config=%s。"
            % (
                state.route,
                direction.get("name", "-") if direction else "-",
                direction.get("methodology_config", "-") if direction else "-",
            ),
        )
        logger.info("research_route_selected scenario_id=%s route=%s default=true", state.scenario_id, state.route)
        return state

    def task_plan_builder(self, state: ResearchState) -> ResearchState:
        route_labels = {
            "market_new_product_skill": ("新品表现分析", "补充新品、上市、渠道、增长等检索词。"),
            "product_competitive_skill": ("产品竞争分析", "补充竞品、定位、渠道、价格、差异化等检索词。"),
            "tech_trend_skill": ("技术趋势研究", "补充技术路线、演进阶段、专利、应用场景等检索词。"),
        }
        analysis_name, rewrite_desc = route_labels.get(state.route or "", ("行业分析", "补充关键实体、时间、市场和证据维度检索词。"))
        state.task_plan = [
            {"id": "scope", "name": "识别分析范围", "description": f"确认当前任务为{analysis_name}，并识别目标实体。", "status": "completed"},
            {"id": "rewrite", "name": "改写检索问题", "description": rewrite_desc, "status": "pending"},
            {"id": "retrieve", "name": "初始证据检索", "description": "执行 ES Hybrid RAG 获取首批语料。", "status": "pending"},
            {"id": "reflect", "name": "关联实体反思检索", "description": "围绕竞品、产品、渠道、供应链实体迭代补证。", "status": "pending"},
            {"id": "synthesize", "name": "分类表格输出", "description": f"按{analysis_name}的核心维度生成表格和有效总结。", "status": "pending"},
            {"id": "cite", "name": "引用校验", "description": "输出引用编号和可查看的语料片段。", "status": "pending"},
        ]
        state.execution_steps.append({"name": "task_plan_builder", "status": "completed", "message": "已生成任务计划。"})
        methodology = self._methodology_for_skill(state.route or "", state.scenario_config or {})
        workflow_steps = methodology.get("workflow") or []
        if workflow_steps:
            self._append_method_trace(
                state,
                "task_plan_builder",
                "方法追踪：方向方法论步骤=%s。"
                % " -> ".join("%s:%s" % (item.get("step_id", "-"), item.get("name", "-")) for item in workflow_steps),
            )
        logger.info("task_plan_built scenario_id=%s plan_steps=%s", state.scenario_id, len(state.task_plan))
        return state

    def query_rewriter(self, state: ResearchState) -> ResearchState:
        scenario = state.scenario_config or {}
        skill = (scenario.get("skills") or {}).get(state.route or "", {})
        methodology = self._methodology_for_skill(state.route or "", scenario)
        prompt_context = {
            "query": state.query,
            "scenario_id": state.scenario_id,
            "route": state.route,
            "analysis_direction": (self._direction_for_skill(scenario, state.route or "") or {}).get("name"),
            "skill": state.route,
            "modules": skill.get("modules") or [],
            "required_evidence": skill.get("required_evidence") or methodology.get("required_evidence") or [],
            "blackboard_instruction_templates": methodology.get("blackboard_instruction_templates") or [],
            "router_methodology": methodology.get("router_methodology") or {},
        }
        prompt = build_query_rewrite_prompt(prompt_context)
        result = call_llm_json(prompt, params={"task": "query_rewrite", **prompt_context})
        rewrite_payload = self._parse_query_rewrite_result(result.get("text", ""), state.query)
        state.rewritten_query = rewrite_payload["rewritten_query"]
        self._mark_plan(state, "rewrite", "completed")
        state.execution_steps.append({"name": "query_rewriter", "status": "completed", "message": state.rewritten_query})
        self._append_method_trace(
            state,
            "query_rewriter",
            "方法追踪：Prompt类型=query_rewrite_structured；LLM_PROVIDER=%s；model=%s；must_terms=%s；expanded_terms=%s；evidence_targets=%s；改写依据=%s。"
            % (
                settings.llm_provider,
                settings.openai_model,
                "、".join(rewrite_payload.get("must_terms") or []) or "-",
                "、".join(rewrite_payload.get("expanded_terms") or []) or "-",
                "、".join(rewrite_payload.get("evidence_targets") or []) or "-",
                rewrite_payload.get("rewrite_reason") or "-",
            ),
        )
        logger.info("query_rewritten scenario_id=%s rewritten_len=%s", state.scenario_id, len(state.rewritten_query or ""))
        return state

    def retrieval(self, state: ResearchState) -> ResearchState:
        try:
            vector = self._encode_query_vector(state.rewritten_query or state.query)
            state.retrieval_results = self.retriever.search(
                state.rewritten_query or state.query,
                query_vector=vector,
                size=settings.retrieval_size,
                candidate_size=settings.retrieval_candidates_size,
                rerank=settings.retrieval_rerank_enabled,
            )
            self._mark_plan(state, "retrieve", "completed")
            state.execution_steps.append({"name": "retrieval", "status": "completed", "message": f"初始检索返回 {len(state.retrieval_results)} 条语料。"})
            self._append_method_trace(
                state,
                "retrieval",
                "方法追踪：检索策略=ES Hybrid RAG；BM25=on；dense_vector=%s；embedding_provider=%s；RRF_k=%s；rerank=%s；index=%s；candidate_size=%s；top_k=%s。"
                % (
                    "on" if vector else "off",
                    settings.embedding_provider,
                    settings.retrieval_rrf_k,
                    "on" if settings.retrieval_rerank_enabled else "off",
                    settings.es_index,
                    settings.retrieval_candidates_size,
                    settings.retrieval_size,
                ),
            )
            logger.info("retrieval_completed scenario_id=%s results=%s", state.scenario_id, len(state.retrieval_results))
        except Exception as exc:
            state.errors.append(f"retrieval_failed: {exc}")
            state.retrieval_results = []
            self._mark_plan(state, "retrieve", "failed")
            state.execution_steps.append({"name": "retrieval", "status": "failed", "message": str(exc)})
            logger.exception("retrieval_failed scenario_id=%s", state.scenario_id)
        return state

    def instruction_selector(self, state: ResearchState) -> ResearchState:
        scenario = state.scenario_config or {}
        skill = (scenario.get("skills") or {}).get(state.route or "")
        if skill:
            state.chosen_skill = state.route
            state.chosen_instruction_modules = skill.get("modules") or []
        else:
            state.chosen_skill = "market_new_product_skill"
            state.chosen_instruction_modules = ["G1", "G4", "G10"]
        state.execution_steps.append({"name": "instruction_selector", "status": "completed", "message": f"选择技能 {state.chosen_skill}。"})
        methodology = self._methodology_for_skill(state.chosen_skill or "", scenario)
        prompt_sources = methodology.get("source_prompts") or {}
        templates = methodology.get("blackboard_instruction_templates") or []
        selected_modules = methodology.get("router_methodology", {}).get("selected_modules") or []
        module_rules = [
            "%s:%s" % (item.get("module_id", "-"), item.get("use_for", "-"))
            for item in selected_modules
            if item.get("module_id")
        ]
        required_evidence = skill.get("required_evidence") or methodology.get("required_evidence") or []
        self._append_method_trace(
            state,
            "instruction_selector",
            "方法追踪：Prompt来源=%s；黑板模板=%s；skill=%s；modules=%s；module_rules=%s；required_evidence=%s。"
            % (
                "；".join("%s=%s" % (key, value) for key, value in prompt_sources.items()) or "-",
                "、".join(templates) or "-",
                state.chosen_skill,
                "、".join(state.chosen_instruction_modules) or "-",
                "；".join(module_rules) or "-",
                "、".join(required_evidence) or "-",
            ),
        )
        logger.info(
            "instruction_selected scenario_id=%s skill=%s modules=%s",
            state.scenario_id,
            state.chosen_skill,
            ",".join(state.chosen_instruction_modules),
        )
        return state

    def skill_executor(self, state: ResearchState) -> ResearchState:
        self._append_skill_method_trace(state, "before")
        if state.chosen_skill == "market_new_product_skill":
            state = market_new_product_skill(state)
            self._mark_plan(state, "synthesize", "completed")
            state.execution_steps.append({"name": "skill_executor", "status": "completed", "message": "已生成分类表格分析结果。"})
            self._append_skill_method_trace(state, "after")
            logger.info("skill_executed scenario_id=%s skill=%s sections=%s", state.scenario_id, state.chosen_skill, len(state.answer_sections))
            return state

        if state.chosen_skill == "product_competitive_skill":
            state = product_competitive_skill(state)
            self._mark_plan(state, "synthesize", "completed")
            state.execution_steps.append({"name": "skill_executor", "status": "completed", "message": "已生成产品竞争分析结果。"})
            self._append_skill_method_trace(state, "after")
            logger.info("skill_executed scenario_id=%s skill=%s sections=%s", state.scenario_id, state.chosen_skill, len(state.answer_sections))
            return state

        if state.chosen_skill == "tech_trend_skill":
            state = tech_trend_skill(state)
            self._mark_plan(state, "synthesize", "completed")
            state.execution_steps.append({"name": "skill_executor", "status": "completed", "message": "已生成技术趋势研究结果。"})
            self._append_skill_method_trace(state, "after")
            logger.info("skill_executed scenario_id=%s skill=%s sections=%s", state.scenario_id, state.chosen_skill, len(state.answer_sections))
            return state

        state.answer = "当前问题类型尚未配置可执行技能。"
        state.confidence = 0.3
        state.follow_up_questions = ["请尝试新品表现、竞争格局或技术路线相关问题。"]
        logger.warning("skill_not_configured scenario_id=%s skill=%s", state.scenario_id, state.chosen_skill)
        return state

    def reflection_agent_node(self, state: ResearchState) -> ResearchState:
        scenario = state.scenario_config or {}
        reflection_config = scenario.get("reflection") or {}
        if not reflection_config.get("enabled", True):
            logger.info("reflection_skipped scenario_id=%s reason=disabled", state.scenario_id)
            return state
        self._append_method_trace(
            state,
            "reflection_agent",
            "方法追踪：反思规则=max_iterations:%s；max_entities_per_iteration:%s；min_new_docs_per_iteration:%s；target_evidence_coverage:%s；retrieval_size_per_entity:%s。"
            % (
                reflection_config.get("max_iterations", "-"),
                reflection_config.get("max_entities_per_iteration", "-"),
                reflection_config.get("min_new_docs_per_iteration", "-"),
                reflection_config.get("target_evidence_coverage", "-"),
                reflection_config.get("retrieval_size_per_entity", "-"),
            ),
        )
        state = self.reflection_agent.run(state)
        self._mark_plan(state, "reflect", "completed")
        state.execution_steps.append({"name": "reflection_agent", "status": "completed", "message": f"反思检索 {state.reflection_iterations} 轮，关联实体 {len(state.related_entities)} 个。"})
        self._append_method_trace(
            state,
            "reflection_agent",
            "方法追踪：反思结果=iterations:%s；related_entities:%s；evidence_coverage:%s。"
            % (
                state.reflection_iterations,
                "、".join(state.related_entities) or "-",
                "、".join("%s=%s" % (key, value) for key, value in state.evidence_coverage.items()) or "-",
            ),
        )
        logger.info(
            "reflection_completed scenario_id=%s iterations=%s related_entities=%s",
            state.scenario_id,
            state.reflection_iterations,
            len(state.related_entities),
        )
        return state

    def synthesis(self, state: ResearchState) -> ResearchState:
        if state.answer:
            return state
        state.answer = "未能生成分析结果。"
        state.confidence = state.confidence or 0.3
        logger.warning("synthesis_fallback_answer scenario_id=%s", state.scenario_id)
        return state

    def citation_checker(self, state: ResearchState) -> ResearchState:
        state.citations = list(dict.fromkeys(state.citations))[:5]
        state.modules_used = list(dict.fromkeys(state.modules_used + state.chosen_instruction_modules))
        if state.reflection_iterations:
            state.modules_used = list(dict.fromkeys(state.modules_used + ["reflection_agent"]))
        state.skills_used = list(dict.fromkeys(state.skills_used))
        state.confidence = state.confidence if state.confidence is not None else 0.5
        self._mark_plan(state, "cite", "completed")
        state.execution_steps.append({"name": "citation_checker", "status": "completed", "message": f"校验引用 {len(state.citations)} 条。"})
        self._append_method_trace(
            state,
            "citation_checker",
            "方法追踪：最终modules_used=%s；skills_used=%s；citations=%s；confidence=%.2f。"
            % (
                "、".join(state.modules_used) or "-",
                "、".join(state.skills_used) or "-",
                "、".join(state.citations) or "-",
                state.confidence or 0.0,
            ),
        )
        logger.info(
            "citation_checked scenario_id=%s citations=%s modules=%s skills=%s confidence=%.2f",
            state.scenario_id,
            len(state.citations),
            len(state.modules_used),
            len(state.skills_used),
            state.confidence or 0.0,
        )
        return state

    def _encode_query_vector(self, query: str) -> Optional[list[float]]:
        return embed_query(query)

    def _mark_plan(self, state: ResearchState, plan_id: str, status: str) -> None:
        for item in state.task_plan:
            if item.get("id") == plan_id:
                item["status"] = status
                return

    def _append_method_trace(self, state: ResearchState, name: str, message: str) -> None:
        state.execution_steps.append({"name": name, "status": "completed", "message": message})
        logger.info("method_trace scenario_id=%s node=%s message=%s", state.scenario_id, name, message)

    def _append_skill_method_trace(self, state: ResearchState, phase: str) -> None:
        methodology = self._methodology_for_skill(state.chosen_skill or "", state.scenario_config or {})
        output_contract = methodology.get("output_contract") or {}
        configured_sections = output_contract.get("sections") or []
        skill_task = {
            "market_new_product_skill": "market_new_product",
            "product_competitive_skill": "product_competitive",
            "tech_trend_skill": "tech_trend",
        }.get(state.chosen_skill or "", state.chosen_skill or "-")

        if phase == "before":
            self._append_method_trace(
                state,
                "skill_executor",
                "方法追踪：Skill执行前；skill=%s；LLM任务类型=%s；输入证据数=%s；configured_sections=%s；output_style=%s；citation_label=%s。"
                % (
                    state.chosen_skill or "-",
                    skill_task,
                    len(state.retrieval_results),
                    "、".join(configured_sections) or "-",
                    output_contract.get("style", "-"),
                    output_contract.get("citation_label", "-"),
                ),
            )
            return

        self._append_method_trace(
            state,
            "skill_executor",
            "方法追踪：Skill执行后；skill=%s；实际sections=%s；skills_used=%s；modules_used=%s；citations=%s；follow_up_questions=%s。"
            % (
                state.chosen_skill or "-",
                "、".join(item.get("id", item.get("title", "-")) for item in state.answer_sections) or "-",
                "、".join(state.skills_used) or "-",
                "、".join(state.modules_used) or "-",
                "、".join(state.citations) or "-",
                "、".join(state.follow_up_questions) or "-",
            ),
        )

    def _direction_for_skill(self, scenario: Dict[str, Any], skill_id: str) -> Optional[Dict[str, Any]]:
        for direction in scenario.get("analysis_directions") or []:
            if direction.get("skill_id") == skill_id:
                return direction
        return None

    def _methodology_for_skill(self, skill_id: str, scenario: Dict[str, Any]) -> Dict[str, Any]:
        direction = self._direction_for_skill(scenario, skill_id)
        if not direction:
            return {}
        config_path = direction.get("methodology_config")
        if not config_path:
            return {}
        path = Path(config_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            with path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            logger.exception("methodology_config_load_failed path=%s skill=%s", path, skill_id)
            return {}

    def _parse_query_rewrite_result(self, text: str, fallback_query: str) -> Dict[str, Any]:
        raw = (text or "").strip()
        fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, flags=re.DOTALL | re.IGNORECASE)
        if fence_match:
            raw = fence_match.group(1).strip()
        try:
            payload = yaml.safe_load(raw) if raw else {}
            if not isinstance(payload, dict):
                raise ValueError("query rewrite result is not a dict")
        except Exception:
            payload = {"rewritten_query": raw or fallback_query}

        rewritten_query = str(payload.get("rewritten_query") or fallback_query).strip()
        must_terms = [str(item).strip() for item in payload.get("must_terms", []) if str(item).strip()]
        expanded_terms = [str(item).strip() for item in payload.get("expanded_terms", []) if str(item).strip()]
        evidence_targets = [str(item).strip() for item in payload.get("evidence_targets", []) if str(item).strip()]
        if fallback_query not in rewritten_query:
            rewritten_query = f"{fallback_query} {rewritten_query}".strip()
        return {
            "rewritten_query": rewritten_query,
            "must_terms": must_terms,
            "expanded_terms": expanded_terms,
            "negative_terms": [str(item).strip() for item in payload.get("negative_terms", []) if str(item).strip()],
            "evidence_targets": evidence_targets,
            "rewrite_reason": str(payload.get("rewrite_reason") or "").strip(),
        }

    def _step_message(self, node_name: str, status: str) -> str:
        labels = {
            "scenario_loader": "加载场景配置",
            "research_router": "识别问题路线",
            "task_plan_builder": "生成任务计划",
            "query_rewriter": "改写检索问题",
            "retrieval": "检索初始语料",
            "instruction_selector": "选择指令和技能",
            "reflection_agent": "反思扩展关联实体",
            "skill_executor": "生成结构化分析",
            "synthesis": "合成最终输出",
            "citation_checker": "校验引用",
        }
        suffix = "中" if status == "running" else "完成"
        return f"{labels.get(node_name, node_name)}{suffix}"
