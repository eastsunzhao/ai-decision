# AI Decision Agent OS

企业级智能知识问答 / 行业分析决策系统 P0 MVP。当前已落地一条可运行链路：

`市场行情分析 -> 新品表现问题 -> ES Hybrid Search -> 新品清单 -> 最近5年时序 -> 表现总结 -> 原因解释 -> 引用输出`

## 已实现范围

- FastAPI `/api/chat` 接口
- LangGraph 工作流，缺少依赖时可退化为顺序执行器
- Elasticsearch BM25 + dense kNN + RRF + heuristic rerank Hybrid RAG
- 场景配置：`market_trend_analysis`
- 技能：`product_competitive_skill`、`tech_trend_skill`；`market_new_product_skill` 作为历史能力保留
- 统一状态：`ResearchState`
- LLM 抽象：`call_llm_json`，默认 mock，可替换真实模型
- 标准响应字段：`answer`、`citations`、`modules_used`、`skills_used`、`confidence`、`follow_up_questions`
- 前端工作台：首屏分析方向选择、问答输入、答案展示、引用、模块/技能、置信度、后续问题
- Reflection Agent：围绕关联实体迭代补充 ES 证据，并输出反思检索轨迹
- 流式接口：`/api/chat/stream` 会逐步输出任务计划、执行步骤和最终结构化结果
- 结构化输出：按表格分类展示新品清单、5年时序、表现判断、原因解释和有效总结
- GPT-5.5：配置 `LLM_PROVIDER=openai`、`OPENAI_MODEL=gpt-5.5`、`OPENAI_API_KEY=...` 后启用真实模型调用
- 运行日志：工程根目录 `log/app.log`，按天轮转，历史日志 gzip 压缩，最多保留 60 天
- 会话持久化：每次对话完成后保存到 `data/sessions.json`，左侧历史会话可点击恢复结果
- 当前用户入口：产品竞争分析、产品技术趋势研究
- 方法论配置：两个分析方向已拆分到 `app/config/analysis_directions/*.yaml`
- Agent/Skill 定义：已拆分到 `docs/agents/agent_definition.md`、`docs/skills/skill_definition.md`

## 目录结构

```text
app/
  main.py
  api/chat.py
  core/config.py
  core/llm.py
  core/state.py
  es/client.py
  es/retrieval.py
  graph/workflow.py
  agents/scenario.py
  agents/reflection.py
  config/analysis_directions/
  core/session_store.py
  skills/market_new_product.py
  skills/product_competitive.py
  skills/tech_trend.py
  prompts/
  scenarios/market_trend_analysis.yaml
  schemas/models.py
  static/index.html
  static/styles.css
  static/app.js
```

## 快速启动

```bash
conda activate conda_python311_14
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`.env.example` 默认填入了用户提供的 ES 地址和账号。需要把 `ES_INDEX` 改成真实文档索引名。

默认 `LLM_PROVIDER=openai`。当前 LLM 使用 Azure OpenAI Chat Completions 兼容接口：

```text
LLM_ENDPOINT=https://gz-eastus2.openai.azure.com/openai/v1/chat/completions
OPENAI_MODEL=gpt-5.4-2026-03-05
LLM_LOG_PAYLOADS=true
LLM_LOG_MAX_CHARS=20000
```

代码会自动把完整 `/chat/completions` 地址归一化为 SDK 需要的 base URL。如果没有配置 `OPENAI_API_KEY`，后端会自动退回 mock 输出，保证本地开发不断线。

Embedding 检索默认读取。查询侧 embedding 必须与 ES 中 chunk 的 embedding 使用同一个模型和维度；例如 ES chunk 使用本地 `paraphrase-multilingual-MiniLM-L12-v2` 时：

```text
EMBEDDING_PROVIDER=local
EMBEDDING_ENDPOINT=
EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING_DEPLOYMENT=<Azure OpenAI 中的 embedding 部署名；非 Azure 可留空>
EMBEDDING_DIMS=384
EMBEDDING_REQUEST_DIMENSIONS=false
EMBEDDING_DEVICE=
EMBEDDING_NORMALIZE=true
HF_ENDPOINT=https://hf-mirror.com
HF_HUB_CONNECT_TIMEOUT=60
HF_HUB_DOWNLOAD_TIMEOUT=120
RETRIEVAL_CANDIDATES_SIZE=24
RETRIEVAL_RERANK_ENABLED=true
```

`EMBEDDING_PROVIDER=local` 使用 `sentence-transformers` 加载 `EMBEDDING_MODEL`。首次运行如果本地没有模型缓存，会由 `sentence-transformers` 下载模型；离线环境可以把 `EMBEDDING_MODEL` 配成已下载模型的本地目录。`HF_ENDPOINT`、`HF_HUB_CONNECT_TIMEOUT`、`HF_HUB_DOWNLOAD_TIMEOUT` 会在加载模型前写入环境变量，用于配置 HuggingFace 镜像和下载超时。`HF_ENDPOINT` 可按网络环境切换为 `https://hf-mirror.com`、`https://huggingface.co`、`https://mirrors.tuna.tsinghua.edu.cn/hugging-face-models` 或其他兼容地址。`EMBEDDING_NORMALIZE` 需要与建库时保持一致。

使用 Azure OpenAI 时，可以改为 `EMBEDDING_PROVIDER=openai` 并配置 `EMBEDDING_ENDPOINT`。SDK 请求里的 `model` 参数实际要填 Azure 资源中的 deployment name，代码会优先使用 `EMBEDDING_DEPLOYMENT`；如果留空，则回退到 `EMBEDDING_MODEL`。因此遇到 `unavailable_model` 时，通常需要在 Azure OpenAI Studio 中确认该资源下已部署 embedding 模型，并把精确部署名填到 `EMBEDDING_DEPLOYMENT`。

项目的 VS Code Python 解释器已配置为：

```text
/opt/anaconda3/envs/conda_python311_14/bin/python
```

可选增强依赖在 [requirements-optional.txt](/Volumes/work/code/code_ai/ai_decision/ai-decision/requirements-optional.txt)。安装 `langgraph` 后会自动启用 LangGraph 编排；未安装时使用内置顺序执行器。

前端工作台：

```text
http://127.0.0.1:8000/
```

测试接口：

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"scenario_id":"market_trend_analysis","query":"达仁堂最近5年市场上的新品表现如何？"}'
```

流式接口：

```bash
curl -N -X POST http://127.0.0.1:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"scenario_id":"market_trend_analysis","query":"达仁堂最近5年市场上的新品表现如何？"}'
```

会话接口：

```bash
curl http://127.0.0.1:8000/api/sessions
curl http://127.0.0.1:8000/api/sessions/{session_id}
```

## Agent 工作流

1. `scenario_loader`：读取预置行业分析场景。
2. `research_router`：根据用户问题选择技能路线。
3. `query_rewriter`：把自然语言问题改写为检索友好查询。
4. `retrieval`：执行 ES 混合检索。
5. `instruction_selector`：选择指令模块和技能。
6. `reflection_agent`：从初始证据中抽取关联实体，围绕公司、产品、品牌、竞品、渠道、供应链等实体迭代补充 ES 证据。
7. `skill_executor`：运行产品竞争分析或产品技术趋势研究技能。
8. `synthesis`：生成最终分析。
9. `citation_checker`：去重、校验引用和模块信息。

## 分析方向

当前“分析方向”和“分析场景”统一为首屏入口。进入工作台后左侧不再重复展示方向或场景选择。

- 产品竞争分析：`product_competitive_skill`，配置见 [product_competitive_analysis.yaml](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/config/analysis_directions/product_competitive_analysis.yaml)
- 产品技术趋势研究：`tech_trend_skill`，配置见 [product_tech_trend_research.yaml](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/config/analysis_directions/product_tech_trend_research.yaml)

后端会根据“竞品、竞争分析、技术趋势、技术路线、路线图”等触发词自动路由。历史保留的 `market_new_product_skill` 不作为当前前端入口展示。

方法论拆分说明见 [analysis_direction_methodology.md](/Volumes/work/code/code_ai/ai_decision/ai-decision/docs/analysis_direction_methodology.md)：

- Router 方法参考 `conf/指令选择Prompt.txt`，用于问题拆解、范围生成和模块选择。
- 工作流与执行模板参考 `conf/黑板 - Agent X - 行研指令集 Consolidated Rev 0506.txt`，复用对比、时序、归因、推理等模板。
- Agent 负责流程决策、状态流转、工具调用和停止条件。
- Skill 负责专项分析方向的结构化产出和引用。

## 会话持久化

[app/core/session_store.py](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/core/session_store.py) 负责保存对话结果：

- 保存位置：`data/sessions.json`
- 保存内容：问题、场景、时间、完整响应、引用语料、结构化表格
- 前端左侧“对话历史”会读取 `/api/sessions`
- 点击历史项会调用 `/api/sessions/{session_id}` 并恢复当次结果
- `.gitignore` 已忽略运行态会话文件，避免把业务对话数据提交到仓库

## 运行日志

日志由 [app/core/logging_config.py](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/core/logging_config.py) 统一初始化：

- 当前日志文件：`log/app.log`
- 轮转策略：每天午夜生成一个历史日志
- 压缩策略：历史日志自动压缩为 `.gz`
- 保留策略：最多保留 60 天，约等于最近 2 个月
- 控制台同步输出，便于本地开发观察
- `.gitignore` 已忽略 `log/`，避免运行日志进入版本库

关键日志覆盖：

- HTTP 请求开始、结束、异常
- `/api/chat` 与 `/api/chat/stream` 请求和流式步骤
- 工作流节点开始、完成、失败
- ES BM25、dense、Hybrid Search 检索耗时和命中数
- LLM / GPT-5.5 调用、fallback 和异常
- Reflection Agent 每轮实体扩展、停止原因和证据覆盖
- 新品表现 Skill 的抽取结果、引用数和置信度

## Reflection Agent

[app/agents/reflection.py](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/agents/reflection.py) 实现了反思扩展检索：

- 从初始检索结果的 `metadata` 和文本中抽取关联实体。
- 按实体出现频次排序，优先搜索高相关实体。
- 每轮围绕关联实体构造扩展查询，再次检索 ES。
- 新文档按 ID 去重后合并回 `ResearchState.retrieval_results`。
- 输出 `reflection.iterations`、`reflection.related_entities`、`reflection.evidence_coverage` 和 `reflection.trace`。

前端右侧“引用语料”边栏会根据分类表格中的引用编号展示对应语料片段，便于核查每类结论的证据来源。

推荐停止规则已写入 [app/scenarios/market_trend_analysis.yaml](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/scenarios/market_trend_analysis.yaml)：

- `max_iterations: 3`：硬上限，避免无限扩张。
- `max_entities_per_iteration: 4`：每轮最多扩展 4 个实体，控制 ES 查询量。
- `min_new_docs_per_iteration: 1`：本轮没有新增文档时提前停止。
- `target_evidence_coverage: 0.75`：覆盖 75% 必要证据维度后提前停止。
- `retrieval_size_per_entity: 3`：每个关联实体最多取 3 条补充证据。

这套规则比单纯固定 3 轮更稳：既允许复杂问题继续扩展，也会在边际收益下降时及时收敛。

## 场景配置

当前场景文件：[app/scenarios/market_trend_analysis.yaml](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/scenarios/market_trend_analysis.yaml)

该场景预置了：

- 数据源：Elasticsearch
- 当前默认路线：`product_competitive_skill`
- 指令模块：`G1/G2/G4/G7/G10/G12/G96`
- 触发词：新品、上市、新品表现、市场反响、竞争分析、竞品、技术趋势、技术路线、路线图等
- 反思检索：最多 3 轮，按关联实体补充证据，目标覆盖产品上市、市场反馈、销售/增长、渠道信号

后续新增“竞争格局分析”“技术路线图”“供应链分析”“政策影响分析”“风险分析”时，建议按同样方式新增 scenario 或 skill。

## ES Hybrid Search

[app/es/retrieval.py](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/es/retrieval.py) 实现：

- BM25：`multi_match` 检索 `text/content/title/summary/metadata.*` 中的关键业务字段。
- dense kNN：通过 [app/core/embeddings.py](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/core/embeddings.py) 生成 query embedding 后检索 ES `embedding` 字段。
- RRF：按倒数排序融合 BM25 和向量结果。
- Rerank：通过 [app/es/rerank.py](/Volumes/work/code/code_ai/ai_decision/ai-decision/app/es/rerank.py) 对 RRF 候选结果做轻量重排，综合 RRF 分、标题命中、正文命中、metadata 命中和精确短语命中。

如果 ES 中的文档向量是 `text-embedding-3-*` 并且建库时指定了 `dimensions=1024`，可以把 `EMBEDDING_REQUEST_DIMENSIONS=true`。如果建库时使用模型默认维度，保持 `false`，避免请求参数与模型能力不匹配。

## 开发路线

P0 已完成：

- FastAPI
- ES 连接配置
- 场景配置
- Hybrid Search 框架
- LangGraph 主流程
- 新品表现 Skill
- 带引用答案

P1 建议：

- 接入真实 `call_llm_json`
- 接入 embedding / reranker
- 将指令集 prompt 存入 `app/prompts`
- 增加 LLM Router，按查询自动选择 G1/G2/G4/G10/G12/G21 等模块
- 增加更细置信度评分

P2 建议：

- 场景配置管理后台
- 分析任务日志
- 报告导出
- 多 Agent 并行执行和结果合成
