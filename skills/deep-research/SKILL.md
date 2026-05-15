## name: deep-research
description: 面向开放式调研、财务分析、市场/公司问题、政策问题以及重证据报告的深度研究工作流。适用于用户需要“经研究得出的答案”而非快速响应的场景，尤其是在任务需要明确范围界定、多步骤研究拆解、迭代式网页检索、来源核验、比较分析或高引用密度终稿时。

# 深度研究

适用场景：需要可引用结论、分步骤证据核验、并行执行与结果汇总的研究任务。

## 非协商执行门槛

- 第一轮必须先完成计划，不得直接调用 `web_search` / `web_fetch` / `data_source_search` 开始取证。
- 计划阶段必须先界定范围、拆解步骤、审视修订，并写入本轮运行目录的 `plan.md`。
- 只有 `plan.md` 已包含研究步骤、研究问题、依赖关系与研究类型后，才可以开始搜索或抓取。
- 若用户问题看似简单但已激活 deep-research，也必须遵守 plan-first；不能退化成 simple workflow。
- 工具调用前可以简短说明计划意图，但不要把计划只停留在自然语言里，必须落盘。
- 计划表中的每个研究步骤都必须由独立 sub-agent 执行；主 Agent 不得直接代替任一步骤做主体研究。
- 每个研究步骤都必须产出对应的 `findings/Sx-主题短名.md`。即使步骤受阻，也必须写入该文件并说明 blocked 原因、已核查来源与剩余缺口。
- 所有必需的 `findings/Sx-主题短名.md` 存在并被主 Agent 阅读前，不得写最终 `findings.md` 或向用户宣称任务完成。
- 完成最终 `findings.md` 后，必须向用户发送一条总结消息，包含核心结论摘要、最终文件链接、各子任务 findings 链接，以及任何证据不足或流程偏离。
- 在带有 session runtime 目录的 Web UI 中，当前工作目录通常已经是本 session 的输出目录。每一轮 deep-research 必须在当前 session 输出目录内新建一个 `deep_research_N/` 运行目录，N 从 0 开始选择最小未使用编号（已有 `deep_research_0/` 和 `deep_research_1/` 时使用 `deep_research_2/`）。所有运行时产物必须写入该目录；不得写入 `skills/deep-research/`、`_agent/skills/deep-research/`，也不得在当前 session 输出目录下再次拼接 `temp/<session_id>/`。

## 运行目录

每轮 deep-research 的目录结构固定为：

```text
deep_research_N/
├── plan.md
├── findings.md
└── findings/
    ├── S1-市场格局.md
    ├── S2-政策风险.md
    └── ...
```

路径规则：

- 新一轮研究开始前，检查当前 session 输出目录下已有的 `deep_research_N/`，选择最小未使用 N。
- 本轮后续所有 plan、子任务 findings、最终 findings 都必须使用同一个 `deep_research_N/`。
- 给 sub-agent 的输出路径必须是 `deep_research_N/findings/Sx-主题短名.md` 这种相对路径。
- `主题短名` 必须用几个字概括该研究步骤主题，优先中文短名；只能包含中文、英文字母、数字、连字符和下划线，不得包含空格、斜杠、冒号或其他路径特殊字符。
- 每个步骤的具体 findings 文件名必须写入 `plan.md` 的 `findings_file` 列；后续派发、验证、重派、汇总和最终链接都必须以该列为准，不要临时改名。
- 不要使用 `skills/deep-research/plan.md`、`skills/deep-research/findings/Sx-主题短名.md` 或 `temp/<session_id>/...` 作为运行产物路径。


## 数据源与取证工具

优先与运行时 `Preferred data source` 一致；它是默认取证偏好，不是硬性执行边界。

- 结构化来源检索优先使用 `data_source_search(source, query)`：
  - `mid_platform`
  - `forum`
  - `news`
- 开放式网页发现与具体页面阅读继续使用 `web_search` / `web_fetch`。

补充规则：

- `news` 查询优先英文关键词；中文请求先翻译成英文再检索
- 若当前偏好数据源证据不足，可使用其他可用工具补证，但必须说明来源选择
- 避免无说明地跨数据源混用证据来源
- 文件存在性、目录枚举与内容读取优先使用 `read_file` / `write_file` / `edit_file` / `glob` / `grep`，不要用 `exec ls/find/cat` 代替文件工具。
- deep-research 流程中除非明确确认目标目录仍在当前 session runtime 目录内，否则不得给 `exec` 显式传入 `working_dir`。特别禁止把 `working_dir` 设置为父级 workspace 根目录。
- shell 命令中避免使用 `2>/dev/null`、`>/dev/null` 等 `/dev/null` 重定向；在 restrict-to-workspace 环境下它可能被绝对路径安全检查拦截。需要容错时改用文件工具或让命令自然返回错误。

## 用户可见进度反馈

deep-research 是长任务。主 Agent 必须用少量用户可见的 assistant 消息解释关键中间状态，让用户知道研究正在推进；这些消息是进度反馈，不是阶段性结论。

反馈规则：

- 计划完成并写入 `plan.md` 后，发送一条简短进度反馈，说明本轮研究目录、步骤数量、第一批将并行启动哪些步骤。
- 每启动一批 sub-agent 后，发送一条简短进度反馈，列出本批步骤 id；不要复述完整 prompt。
- 每收到一批 sub-agent 结果并完成 `read_file` 验证后，发送一条简短进度反馈，说明已验证哪些 `findings/Sx-主题短名.md`，下一步是等待依赖、补证、启动下一批，还是开始汇总。
- 若仍有 sub-agent 运行中，而主 Agent 正在等待结果，最多发送一条等待状态反馈，说明正在等待哪些步骤；不要重复刷屏。
- 若某步骤 blocked、验证失败或需要重派，必须发送一条简短进度反馈，说明哪个步骤受阻以及下一步处理方式。
- 最终报告写入 `findings.md` 后，发送完成总结；完成总结不受本节“简短反馈”限制，仍按 C. 产出报告执行。

内容限制：

- 每条中间进度反馈最多 2 句。
- 中间进度反馈只描述流程状态、已验证文件和下一步动作；不得提前给出未经最终整合的业务结论。
- 不要把 sub-agent 的详细发现、长引用、表格或半成品分析流式输出给用户；这些内容必须保存在 `findings/Sx-主题短名.md` 和最终 `findings.md` 中。
- 如果没有新的状态变化，不要发送进度反馈。

## 执行流程

### A. 计划

1. 围绕用户问题定义研究范围。
  明确优秀输出必须包含什么、必须解释/比较/证明什么，以及最重要的边界条件。
2. 将范围拆解为关键研究步骤。
  将问题分解为能高质量回答用户所需的最小高价值步骤集合。规划阶段不要引入参考 ID。
  设计步骤时应优先拆成可独立取证、可并行执行的子问题；只有后续步骤确实需要前序 findings 的事实结果时，才设置依赖关系。
3. 审视并修订步骤规划。
  对首版拆解进行压力测试，检查是否存在维度缺失、逻辑薄弱、内容重叠、顺序错误或不必要的依赖，并在研究前完成修订。
4. 简洁输出最终研究步骤（S）。
  最终步骤数量必须小于 6。  
   保持步骤简短、可用于决策、且彼此区分明确。
5. 为每个步骤构造一个精确研究问题（Q）。
  每个问题应清晰、完整、无歧义，足以驱动聚焦搜索，不依赖隐含指代。
6. 评估步骤间依赖关系。
  判断每个步骤是否可独立研究（D）。默认保持 `depends_on` 为 `[]` 以便并行；若依赖前序步骤，需明确写出依赖哪些步骤，并确保该依赖不可通过在子任务提示中提供背景范围来消除。
7. 为每个问题分类研究类型（见 `Research Type Classification`）。
  在收集来源前，用该分类确定证据形态、搜索策略与综合方法。
8. 将深度研究计划保存为本轮运行目录下名为 `plan.md` 的 Markdown 文件。
  内容应包含第 4 步中的步骤（S）和第 5 步中的问题（Q），并附加第 6 步中的依赖关系以及第 7 步中的分类。如果 finding.md 已存在，则清空该文件。
9. 产出 `plan.md` 的 Task Table，列为：
`| id | task | question | depends_on | research_type | findings_file |`
   `depends_on` 空值统一写 `[]`。`research_type` 是研究方法论字段，必须保留；不要求维护运行时状态字段、尝试次数或 owner。
   `findings_file` 必须是相对本轮运行目录的路径，例如 `findings/S1-市场格局.md`，文件名格式为 `Sx-主题短名.md`，短名用几个字概括调研主题。

### B. 并行执行

1. 主 Agent 阅读 `plan.md`，判断哪些研究步骤互不依赖，可以同时执行。
2. 对没有相互依赖的步骤，必须使用 `spawn` 并行启动 sub-agent；只有在工具不可用或明确失败时，才允许降级，并必须在最终总结中说明原因。
3. 启动本批 sub-agent 后，主 Agent 必须发送一条用户可见进度反馈，格式简洁，例如：`已启动 S1/S2/S3 并行研究；我会先等待它们写入并验证 findings 文件。`
4. 每个 sub-agent 只负责一个明确子问题，并应获得：
  - 对应研究步骤和研究问题
  - 研究类型
  - 运行时 `Preferred data source`
  - 必要的依赖步骤摘要
  - 明确的输出文件路径，优先使用 workspace-relative 路径
  - 完成条件：必须写入并读回验证该步骤在 `plan.md` 中指定的 `findings/Sx-主题短名.md`
5. 每个 sub-agent 自行决定搜索关键词、放松方式、来源切换和迭代策略；主 Agent 不预设，也不要求记录这些过程。
6. 每个 sub-agent 仅写自己的 `deep_research_N/findings/Sx-主题短名.md`，不得写总 `findings.md` 或最终报告。
7. 主 Agent 发给 sub-agent 的 prompt 必须包含以下完成契约：
  - 将研究结果写入指定的 `findings/Sx-主题短名.md`。
  - 写入后必须使用 `read_file` 读取该文件，确认文件存在且内容是本步骤结果。
  - 任务完成条件不是返回消息，而是指定 findings 文件存在、非空、可被 `read_file` 读回，并且内容属于本步骤。
  - 若搜索、抓取或证据不足导致步骤受阻，也必须写入 blocked 版本的 `findings/Sx-主题短名.md`，说明受阻原因、已核查来源和剩余缺口。
  - 如果 `findings/Sx-主题短名.md` 未成功写入并验证，不得声明 completed / done / success。
  - 不要用 `exec` 检查、读取或枚举 findings 文件；使用 `read_file`、`write_file`、`glob`。
8. 若步骤受阻，sub-agent 必须写入以下 blocked 版 `Sx-主题短名.md` 模板：

```md
# Sx: <任务名>

## Status
blocked

## Blocker
<搜索、抓取、权限、证据不足等阻塞原因>

## Sources Checked
<已实际检查的来源、工具结果或查询方向>

## Partial Findings
<已经能确认的信息；没有则写“无可确认结论”>

## Remaining Gaps
<还缺哪些证据或后续应如何补证>
```

9. sub-agent 不需要向主 Agent 汇报研究过程；完成时只需给出以下结构化完成信号，实质结果以 `findings/Sx-主题短名.md` 为准：

```text
status: done | blocked
findings_file: deep_research_N/findings/Sx-主题短名.md
verified: yes | no
note: 一句话说明
```

   `verified: no`、缺少 `findings_file`，或完成信号与文件状态不一致时，main Agent 不得视为完成。
10. 主 Agent 在 sub-agent 完成后必须使用 `read_file` 读取对应 findings 文件；若文件不存在、为空、不是该步骤结果，或未包含 `Status`，应将该步骤视为未完成。
11. 每批 sub-agent 完成并读回验证后，主 Agent 必须发送一条用户可见进度反馈，说明已验证的步骤和下一步动作。例如：`已验证 S2/S3 的 findings 文件；接下来等待 S5，并准备启动依赖它们的下一批研究。`
12. 未完成时，主 Agent 最多重新派发同一步骤一次。重派 prompt 必须明确要求先补写/修复指定 `Sx-主题短名.md`，不得继续泛泛研究；若补写仍失败，主 Agent 必须自行写入 blocked 版 `Sx-主题短名.md`，说明“subagent 未能交付文件”，并在最终总结中披露流程偏离。
13. 主 Agent 在每批结果完成后汇总 `deep_research_N/findings.md`；汇总应服务于最终判断，不需要记录调度变量。
14. 若某一步没有查到足够数据，优先按照当前偏好数据源补证；若扩展到其他来源，说明原因。
15. 对存在依赖关系的任务，按依赖波次执行：先并行启动所有依赖已满足的任务，等待并读取对应 `findings/Sx-主题短名.md`，再启动下一批任务。
16. 主 Agent 的补充检索只能用于核验、填补缺口或汇总，不得替代计划表中任何研究步骤的 sub-agent 产出。

### C. 产出报告

仅在必需步骤 `done`（或有充分理由 `blocked`）后出最终结论。  
报告中必须区分“事实”与“推断”，并附来源链接。
报告中不允许出现任何数据 ID 数据。
写入最终 `deep_research_N/findings.md` 后，必须发送用户可见的完成总结，不得只写文件不回复。总结必须提供最终报告文件链接，并列出所有 `findings/Sx-主题短名.md` 子任务文件链接。

## 研究类型分类（Research Type Classification）

将每个研究问题归入以下最匹配类别之一：

- `Enumeration`：构建完整清单、分类体系或格局地图
- `Comparison`：按共享维度对实体进行比较
- `Reasoning / Deduction`：基于约束、假设或前提推导结论
- `Timeline`：重建时间序列并识别关键转折点
- `Geography`：解释空间分布与区域差异
- `Risk`：识别、评估并缓释风险
- `Relations / Network`：描绘参与方及其关系网络
- `Framework-led Analysis`：应用结构化分析框架
- `Case Study`：从具体案例提炼经验
- `Causal Attribution`：解释结果为何发生
- `Planning / Strategy`：评估路径、权衡与成功条件
- `Industry / Supply Chain`：刻画价值流、瓶颈与权力结构

## 质量底线

- 先界定范围，再开展搜索。
- 若首版拆解较弱，执行前先修订研究计划。
- 不得引用未实际核查的来源。
- 少量引用，重在综合。
- 相比浅层广覆盖，优先完整且可决策的覆盖深度。
- sub-agent 自行控制检索成本、上下文占用和迭代深度；主 Agent 不预设固定轮次或候选数量。
- 证据薄弱、混杂或过时时，必须直接说明。
- 若用户要求深度研究，应优先保证来源质量、结构完整性与可辩护的综合结论，而非追求简短。
- 使用较长段落，避免过度碎片化短句和点列。
