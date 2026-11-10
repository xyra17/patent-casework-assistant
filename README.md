# Patent Casework Assistant · 探渊

专利代理部门的案件与文档工作台：查案件信息、核对计划期限、整理会议待办、检索内部参考、统计业务量。答案保留来源、文件版本与未完成原因。

公开仓库只保留运行代码、前端、依赖锁、必要测试及模拟数据生成器，不包含本机评测raw、历史成绩报告、真实业务数据、数据库、模型权重或密钥。

## 当前业务范围

当前提供19个自动业务入口。主要能力是案件查询、会议记录、期限排查、部门统计、内部参考检索及附件程序统计。

| 入口 | 范围 |
|---|---|
| 案件信息与会议待办 | 负责人、案件状态、登记待补事项和来源位置；不承诺专业技术机制解释 |
| 上传表格分析与图表 | 程序过滤、主键去重、聚合、未知值和图表一致性校验 |
| 两版技术文档对比、上传交底找参考、公开技术资料检索 | 已退出独立自动入口，转人工；共享通用工具保留给其他流程 |

`VERIFIED`表示完成对应内部核验，`REVIEW_REQUIRED`表示专业判断待人工，`INCOMPLETE/BLOCKED`表示缺材料、校验或交付未完成。任务执行结束不代表业务通过。系统没有无后端记录支持的“人工已确认”按钮。

## 本地运行

需要Python 3.12+、uv、Node.js 22及Docker（运行MySQL）。在仓库根目录执行：

```bash
uv sync --locked
cp .env.example .env
npm --prefix frontend ci
npm --prefix frontend run build
# 首次准备上下文计量词表（静态资源，无模型调用）
.venv/bin/python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'
```

编辑`.env`，填写`DEEPSEEK_API_KEY`、`MYSQL_PASSWORD`及`MYSQL_DEV_ROOT_PASSWORD`。默认配置使用DeepSeek官方OpenAI兼容端点；真实Agent任务需要有效模型凭据和额度。可改为获准的本地或内网端点。

```bash
docker compose up -d mysql
# 等待MySQL健康后，只在首次初始化本地演示库时执行：
PYTHONPATH=. .venv/bin/python scripts/setup_department_demo.py --activate
.venv/bin/python main.py
```

访问`http://localhost:8000`，健康检查为`/api/health`。激活脚本仅面向本地开发MySQL，会生成34案独立模拟资料、创建演示库与分离权限账号，并备份旧配置和应用库；不要把它当作已有生产库的升级命令。

可先询问：

- “DEMO-2026-013由谁负责？三个计划期限是什么？给出来源。”
- “客户甲（模拟）与IPR林（模拟）以前开过哪些会议，待补什么？”
- “2026年9月和10月实际收案、交局各多少？未知日期另列。”
- 上传自己的模拟CSV，按实际表头统计并查看图表。

公司知识库默认未接入材料；找不到内部规定时应说明缺口。不要把演示资料或网上说明当公司制度。

## 检索、解析与权限

默认关闭案件向量分支，可先用结构化查询和词法检索运行。准备本地`Qwen/Qwen3-Embedding-0.6B`、`bge-reranker-base`权重并配置路径后，可启用`CASEWORK_EMBEDDING=1`及重排。公司文档索引同样需要本地Embedding权重；运行时不会把缺失权重冒充检索成功。

Word、Excel、CSV和文字PDF使用本地解析；扫描件、图像与视觉模型路径另见[文档解析](docs/document-parsing.md)。联网默认关闭，开启需要有效Tavily配置，并受本次来源权限限制。

固定流程与DeepAgents共享证据、Validator和交付检查。SQL查询只读；维护、导入与SQL控制台使用独立账号和权限，默认不开放写入。涉及私密资料应配置获准的内网模型和资料目录。

## 离线测试

不需要模型API、不调用付费服务。首次运行前按上面的步骤缓存Tokenizer词表；测试期间禁止外部网络连接。数据不存在时测试入口会生成独立模拟资料：

```bash
uv sync --locked
uv pip install pytest
.venv/bin/python scripts/test_backend_offline.py
.venv/bin/python -m pytest -q tests/test_attachment_comparison.py tests/test_casework_vertical_forms.py tests/test_multimodal_documents.py tests/test_result_assessment.py tests/test_verification_false_blocks.py tests/test_statistics_check.py tests/test_product_boundary.py tests/test_scope_trim.py
npm --prefix frontend test
npm --prefix frontend run build
```

程序测试检查实现与已知边界，不是独立专家审核或真实业务成功率。本仓库不发布缺少原始证据的业务通过率。

## 代码导航

- `api/`：任务、会话、证据和业务管理接口。
- `casework/`：案件投影、来源、检索、统计和业务契约。
- `agent/`、`harness/`：规划、执行、上下文预算与有界修正。
- `tools/`、`rules/`：工具、来源核验、统计交付和长期规则。
- `frontend/`：Vue工作台、执行记录、来源与图表。
- `scripts/`、`examples/department_shared_drive/`、`sql/`：本地初始化、模拟资料生成与必要校验。
