# Patent Casework Assistant

面向专利代理部门的案件与文档分析工作台。代理师可以在这里查案卷、核对办理记录、准备客户会议；部门负责人可以查看业务量和分案参考。结果尽量带回案号、原文位置和文件版本，方便回到材料核实。

![专利代理部门案件分析工作台首页截图](docs/images/homepage-20261105.png)

*首页截图展示模拟业务场景。*

![模拟扫描交底任务的执行结果、来源入口与执行过程](docs/images/scanned-disclosure-result-20261106.png)

*模拟扫描交底任务：左侧为会话，中间为带原文位置的结果，右侧为执行过程。*

## 典型业务任务

工作台主要处理散落在共享盘和业务记录中的案件资料。下面是当前界面和任务入口覆盖的几类工作；附件分析、公开资料检索、图表和报告是完成这些工作的方式。

| 场景 | 用户可能这样问 | 系统提供什么 |
| --- | --- | --- |
| 交底与旧案对照 | “我上传了新交底，我们以前办过类似机制的案子吗？” | 从交底提取待核对的问题，按案找技术原文，列出相似点和资料缺口 |
| 案件办理核对 | “这个案子谁负责？初稿、送审和交局期限分别是什么？” | 汇集案件信息、期限和原文出处；缺失或冲突的字段单列 |
| 客户会议准备 | “以前谁和这位客户 IPR 开过会？当时还缺什么材料？” | 按场会议列出日期、参会人、关联案件和待补事项 |
| 部门统计与汇报 | “9 月和 10 月各收了多少案、实际交局多少件？” | 按实际日期核对数量和明细；按任务需要生成图表或报告 |
| 辅助分案 | “谁办过类似技术？他们手头的案量和近期期限怎样？” | 提供有原文依据的经历、登记负荷和期限，供负责人决定 |

“案件”页面适合按已有口径查资料；会话入口可以处理附件、跨来源比较和报告任务。明确字段由程序直接查询，固定任务按预定步骤执行，需要动态探索的任务由 DeepAgents 根据返回结果选择下一步。所有执行策略都受本次任务的数据源设置约束。

## 业务全景

![业务全景：使用者、案件任务、资料接入、结果交付与人工办理](docs/diagrams/00-business-overview.svg)

## 系统结构

![系统组件架构：工作台、Python 后端、执行器、任务级缓存、模型与存储](docs/diagrams/01-system-architecture.svg)

后端在同一个 FastAPI 应用内组织任务，固定业务执行器与 DeepAgents 共享数据访问、来源记录和文件工具。同一任务已取得且版本一致的案件资料可以复用；会前准备按案收集材料清单与已发布正文。具体实现见[任务执行](docs/execution-boundaries.md)和[来源权限](docs/task-source-selection.md)。

### 固定流程与 Agent 如何分工

| 执行策略 | 谁决定下一步 | 适合的任务 |
| --- | --- | --- |
| 直接查询 | 完整句式匹配后，由程序取数并输出 | 同一案件的负责人、阶段和多个期限字段 |
| 固定流程 | 程序按既定步骤读取和计算，模型按需理解或整理 | 期限排查、月度统计、固定会前卡 |
| 固定流程加条件分支 | 固定读取起步，再按结果筛选、补查或澄清 | 相似案件查找、辅助分案 |
| Agent 动态执行 | DeepAgents 根据已取得的结果选择后续工具，按需委派子智能体 | 阅读附件后比较旧案、跨来源分析、整理报告 |

这四种策略共享执行设施，不是依次经过的四层。程序按完整需求和已开放来源选择策略；缺少资料或关键条件时，保留缺口并请用户补充。

<details>
<summary>展开：任务执行流程</summary>

![四种执行策略、用户补充、有界修正与结果交付](docs/diagrams/02-routing-and-execution.svg)

</details>

<details>
<summary>展开：任务运行时序</summary>

![任务提交、执行、资料复用、持久化与事件推送时序](docs/diagrams/04-task-sequence.svg)

前端先连 WebSocket，再提交任务。后台先持久化执行事件，再推送进度；连接中断后，前端可读取运行记录恢复显示。关键条件不清楚时，任务等待用户补充。

</details>

### 数据放在哪里

| 数据 | 当前存储 | 用途 |
| --- | --- | --- |
| 会话、执行事件与证据 | 应用 SQLite | 恢复任务进度和查看结果来源 |
| 案件目录、原件版本与正文分块 | 案件 SQLite 与本地文件 | 保留案号、原文位置和版本 |
| 结构化业务记录 | MySQL | 查询案件信息、会议和统计所需字段 |
| 公司文档知识库 | Chroma 与词法索引 | 检索单独导入的公司文档 |

案卷和公司文档分别建索引，检索范围也各自独立。

### 文件如何进入结果

<details>
<summary>展开：文档入库与在线检索</summary>

![共享盘入库、案卷与公司文档检索、按案材料收集及版本复用](docs/diagrams/03-ingestion-and-retrieval.svg)

</details>

共享盘原件按白名单只读扫描；案件信息与会议字段保留候选值及冲突，案卷正文保存分块、位置、哈希和解析版本。当前案卷召回使用本地 **Qwen3-Embedding-0.6B + BM25**，条件满足时使用 **bge-reranker-base** 重排；普通知识库独立维护 Chroma 与词法索引。向量不可用时，部分查询可退回词法路径并显示状态。

文本 PDF 优先读取原生文字；扫描页、图片和复杂版面按配置使用本地 MinerU 解析。纯图形可由单独的视觉工具观察。详见[文档解析](docs/document-parsing.md)。五张图的 SVG、PNG、Mermaid 源及生成命令见[图稿说明](docs/diagrams/README.md)。

## 运行演示

环境：Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node.js 22、Docker。以下命令均在本 README 所在目录执行。

```bash
uv sync --locked
cp .env.example .env
npm --prefix frontend ci
npm --prefix frontend run build
docker compose up -d mysql
```

在 `.env` 中配置业务数据库连接及生成模型的 OpenAI 兼容接口。生成任务需要可用的模型端点；本地向量模型权重位于被 Git 忽略的 `models/`，新克隆需按 `.env.example` 准备 `EMBEDDING_MODEL_PATH` 等路径。保密资料应连接获准的本地模型端点。

首次建立**较小的部门模拟场景**时，运行：

```bash
PYTHONPATH=. .venv/bin/python scripts/setup_department_demo.py --activate
.venv/bin/python scripts/build_knowledge_base.py
.venv/bin/python main.py
```

启动后访问 `http://localhost:8000`，健康检查为 `/api/health`。激活脚本会备份本机旧配置和应用库，并创建演示数据库与分离账号；它只在首次初始化时运行。同步与部署细节见[部署说明](docs/department-deployment.md)。MinerU 本地环境按需使用 `scripts/setup_mineru.sh` 安装。

## 本地检查

运行后端测试与前端构建：

```bash
.venv/bin/python scripts/test_backend_offline.py
npm --prefix frontend test
npm --prefix frontend run build
```

## 代码导航

| 目录 | 职责 |
| --- | --- |
| [`frontend/`](frontend/) | Vue 3 工作台、来源和执行详情、图表与报告 |
| [`api/`](api/) | FastAPI、WebSocket、会话、附件和管理接口 |
| [`casework/`](casework/) | 案件信息、共享盘同步、检索、统计与推荐 |
| [`agent/`](agent/) · [`harness/`](harness/) | DeepAgents 编排与上下文预算 |
| [`tools/`](tools/) · [`rules/`](rules/) | 数据访问、证据核验、文档工具与长期规则 |
| [`deploy/`](deploy/) · [`docs/`](docs/) | 部署配置与详细设计 |

本仓库不包含 `.env`、运行数据库、会话附件或本地模型权重。
