# 项目图稿

五张图分别说明业务、组件、执行、资料与时序。README 展示业务全景和系统架构，其余视图按需展开。

| 图 | 内容 | 文件 |
| --- | --- | --- |
| 00 业务全景与人机协作 | 使用者、主要任务、资料接入与业务人员办理 | [SVG](00-business-overview.svg) · [PNG](00-business-overview.png) · [Mermaid](00-business-overview.mmd) |
| 01 系统组件架构 | 工作台、Python 后端、执行器、模型和存储 | [SVG](01-system-architecture.svg) · [PNG](01-system-architecture.png) · [Mermaid](01-system-architecture.mmd) |
| 02 任务执行与结果核验 | 四种执行策略、用户补充、处理与交付 | [SVG](02-routing-and-execution.svg) · [PNG](02-routing-and-execution.png) · [Mermaid](02-routing-and-execution.mmd) |
| 03 文档入库与在线检索 | 入库、发布、检索、按案材料收集与版本复用 | [SVG](03-ingestion-and-retrieval.svg) · [PNG](03-ingestion-and-retrieval.png) · [Mermaid](03-ingestion-and-retrieval.mmd) |
| 04 任务运行时序 | 任务提交、执行、持久化、事件推送与断线恢复 | [SVG](04-task-sequence.svg) · [PNG](04-task-sequence.png) · [Mermaid](04-task-sequence.mmd) |

SVG 是发布图稿，PNG 为双倍分辨率导出，Mermaid 表达同一主干逻辑。修改布局或文字时，编辑生成脚本并同步 Mermaid。

## 生成

在仓库根目录运行，仅生成 SVG 时无需额外依赖：

```bash
python3 scripts/render_architecture_figures.py
```

导出 PNG 需要 Node.js、Playwright 和 Chromium：

```bash
npm install --no-save --package-lock=false playwright
npx playwright install chromium
node scripts/export_architecture_figures.cjs
```

可以通过 `DIAGRAM_BROWSER` 指定已有 Chromium，通过 `NODE_PATH` 指定 Playwright 模块目录。导出时同时检查画布边界和节点文字内边距。

## 维护入口

- 组件：`api/server.py`、`agent/main_agent.py`、`api/storage.py`。
- 执行：`casework/routing.py`、`casework/execution_policy.py`、`casework/workflow.py`。
- 资料：`casework/sync.py`、`casework/search.py`、`casework/materials.py`、`casework/read_cache.py`。
- 时序：`api/evidence.py`、`api/monitor.py`、`frontend/src/App.vue`。
