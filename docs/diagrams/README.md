# 架构图维护

README 使用三张可编辑 SVG。它们由标准库脚本生成，避免图片中文字、箭头或模型名称在多次手工修改后偏离实现。

```bash
python3 scripts/render_architecture_figures.py
```

| 图 | 要回答的问题 | 核对入口 |
| --- | --- | --- |
| [01 系统架构](01-system-architecture.svg) | 任务从哪里进入、如何执行与交付 | `api/server.py`、`casework/routing.py`、`agent/main_agent.py` |
| [02 路由与核验](02-routing-and-execution.svg) | 来源限制、固定契约与 Agent 的边界 | `casework/workflow.py`、`api/round_budget.py`、`tools/reflect_check.py` |
| [03 解析与检索](03-ingestion-and-retrieval.svg) | 案件信息和两条文档检索路径 | `casework/sync.py`、`casework/search.py`、`tools/document_parser.py`、`tools/knowledge_base_tool.py` |

图只表达系统级流程，不表示跨 SQLite 与 MySQL 的强事务，也不把 OCR 结果当作图形语义或准确的业务事实。更改模型、解析器或执行路径时，同步更新脚本、SVG 和 README 文字，并在普通 README 宽度下检查文字与箭头。
