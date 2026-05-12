# 分析方向方法论拆分

当前“分析方向”和“分析场景”统一为同一个入口。用户只在首屏选择分析方向，进入工作台后不再二次选择场景。

## 来源

- Router 方法参考：`conf/指令选择Prompt.txt`
- 工作流与执行模板参考：`conf/黑板 - Agent X - 行研指令集 Consolidated Rev 0506.txt`

## 拆分原则

- Router 配置只描述如何把用户问题变成研究范围、如何选择分析模块。
- Agent 文档描述流程决策、状态流转、工具调用和停止条件。
- Skill 文档描述专项分析产出，不承担全局编排。
- 分析方向 YAML 连接入口、Skill、模块、证据要求、反思规则和输出结构。

## 当前入口

- 产品竞争分析：`app/config/analysis_directions/product_competitive_analysis.yaml`
- 产品技术趋势研究：`app/config/analysis_directions/product_tech_trend_research.yaml`

后续新增方向时，优先新增一个独立 YAML，再补充或复用对应 Skill；只有当流程决策发生变化时才新增 Agent。
