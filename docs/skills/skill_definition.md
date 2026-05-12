# Skill 定义

Skill 是系统里的“专项分析执行器”。它接收 `ResearchState` 中已经准备好的问题、路由结果、证据和指令模块，输出结构化分析结果、引用、置信度和后续问题。

## 职责边界

- 负责某个明确分析方向的业务逻辑和输出结构。
- 负责把检索语料组织成可读的分类表格、结论和有效总结。
- 负责声明自己使用的分析模块，例如 `G2/G7/G10` 或 `G4/G12/G96`。
- 负责把引用映射为用户可读的 `title`，并生成右侧可查看的语料详情。

Skill 不负责全局路由、跨 Skill 编排、长期记忆、是否继续检索等流程决策。

## 当前 Skill

- `product_competitive_skill`：产品竞争分析。使用 `G2` 对比、`G7` 实体关系映射、`G10` 归因，输出竞争范围、对比矩阵、差异化与壁垒、有效总结。
- `tech_trend_skill`：产品技术趋势研究。使用 `G4` 时序、`G12` 产业链/生态映射、`G96` 技术创新材料综合，输出技术范围、演进阶段、趋势驱动、有效总结。
- `market_new_product_skill`：历史保留 Skill，当前不作为前端入口展示。

## 配置位置

- 产品竞争分析配置：`app/config/analysis_directions/product_competitive_analysis.yaml`
- 产品技术趋势研究配置：`app/config/analysis_directions/product_tech_trend_research.yaml`
- Skill 实现：`app/skills/product_competitive.py`、`app/skills/tech_trend.py`

## 输出契约

每个 Skill 应尽量输出：

- `answer_sections`：分类表格块，前端可展开查看引用语料。
- `citations`：面向用户展示的来源标题。
- `citation_details`：右侧边栏展示的原文片段。
- `modules_used`：使用的分析模块。
- `skills_used`：使用的 Skill。
- `confidence`：结合证据数量、覆盖率和 LLM 结果给出的置信度。
- `follow_up_questions`：建议用户进一步追问的问题。
