# Agent 定义

Agent 是系统里的“流程型决策者”。它不直接等同于一个提示词，也不等同于一个单点函数，而是负责在 `ResearchState` 上推进状态、选择路径、调用工具、协调 Skill，并判断什么时候继续检索或结束。

## 职责边界

- 负责拆解问题、规划任务、选择分析模块和执行顺序。
- 负责维护中间状态，包括计划、执行步骤、路由、证据、反思轨迹、错误和最终校验结果。
- 负责调用外部工具，例如 Elasticsearch Hybrid Search、LLM JSON 接口、引用校验。
- 负责决定是否进入反思循环，以及什么时候停止。

Agent 不应承担某个垂直分析方向的全部业务产出。业务产出应交给 Skill 完成。

## 当前 Agent

- `AgentWorkflow`：主工作流 Agent，串联 `scenario_loader -> research_router -> task_plan_builder -> query_rewriter -> retrieval -> instruction_selector -> reflection_agent -> skill_executor -> synthesis -> citation_checker`。
- `research_router`：按照分析方向配置和触发词选择 Skill，并映射到 `G2/G4/G7/G10/G12/G96` 等模块。
- `ReflectionAgent`：基于已检索语料抽取关联实体，围绕竞品、渠道、供应链、技术、品牌等关系继续从 ES 补证。

## 反思结束规则

默认采用“上限 + 增益 + 覆盖率”组合规则：

- 最多 3 轮，避免开放式检索拖慢响应。
- 每轮至少新增 1 条有效语料，否则停止。
- 关键证据覆盖率达到 75% 后停止。
- 如果一轮没有新增关联实体或新增语料，提前停止。

这个规则比单纯固定 3 轮更稳，因为它允许证据足够时提前结束，也能在证据不足但仍有增益时继续补一轮。

## 配置位置

- 分析方向配置：`app/config/analysis_directions/*.yaml`
- 场景入口配置：`app/scenarios/market_trend_analysis.yaml`
- 工作流实现：`app/graph/workflow.py`
- 反思 Agent 实现：`app/agents/reflection.py`
