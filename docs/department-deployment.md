# 部门共享盘场景部署与恢复

## 本机独立模拟环境

在项目目录准备依赖和本地 MySQL，并在 `.env` 中配置开发用 root 连接及本机 Qwen3-Embedding-0.6B 模型路径。首次运行前先阅读根目录 README 的安装步骤。

1. 仓库已包含 `examples/department_shared_drive/source` 中的模拟资料，无需重新生成。`scripts/build_department_demo.py` 仅用于另行重建演示资料；已有目录时默认拒绝覆盖。
2. `PYTHONPATH=. .venv/bin/python scripts/setup_department_demo.py --activate` 建立 `department_workbench`、`department_management`、独立账号和 SQLite，备份旧配置后切换演示环境、解析并镜像。
3. 将需要单独检索的公司文档放入 `知识库文件/90_公司文档`，运行 `.venv/bin/python scripts/build_knowledge_base.py` 发布索引。案件原件由案卷索引管理。
4. `npm --prefix frontend ci && npm --prefix frontend run build`，再运行 `.venv/bin/python main.py`。
5. 打开“案件”页面，依次核对同步、字段冲突、相似案件、会议、月报和辅助分案。演示配置的自动同步间隔为 300 秒，也可手动触发同步。

前端更新会保留 `frontend/dist/assets` 中已有的带哈希资源，供更新前已打开的页面继续加载执行详情、执行记录等异步组件。在线更新时不要先删除 `dist`，也不要使用会删除旧资源的同步选项；旧资源只在维护窗口、确认旧页面均已关闭或刷新后清理。若页面已经遇到旧资源 404，保留未提交内容后刷新页面即可取得新入口。

初始化脚本只接受 localhost 开发 MySQL，并创建独立的演示库和账号。重复激活会增加新角色；账号仅写入本机 `.env`（600 权限）。

## 内网接入配置

先将应用、依赖和Embedding/生成模型部署在获准内网；离线准备模型和转换器，不在接入私密材料后自动下载。

| 配置 | 含义 |
| --- | --- |
| DATA_MODE=intranet | 强制启用外发阻断 |
| DEEPSEEK_BASE_URL | 内网OpenAI兼容生成模型端点；环境变量保留旧名称，不限定供应商 |
| DEEPSEEK_MODEL / DEEPSEEK_API_KEY | 内网模型名与其访问凭据 |
| LOCAL_MODEL_HOSTS | 明确批准的内网推理主机名，逗号分隔；不要填公网主机 |
| EMBEDDING_MODEL_PATH / CASEWORK_EMBEDDING_MODEL_PATH | 本机已存在的 Qwen3-Embedding-0.6B 目录；案卷路径为空时沿用前者 |
| CASEWORK_DB_PATH | 案卷 SQLite 文件路径，保存解析历史、确认与审计记录 |
| CASEWORK_ALLOWED_ROOTS | 获准共享盘挂载目录的JSON数组；不要允许整个home或磁盘根 |
| CASEWORK_SYNC_INTERVAL | 周期扫描秒数，0关闭；实际执行限制30–86400 |
| CASEWORK_MYSQL_MIRROR | 1启用结构化投影；0只用本地工作台并明确镜像停用 |
| MYSQL_CASEWORK_SYNC_USER / PASSWORD | 独立同步账号，只对cw表有CRUD，不能交给Agent |

共享盘挂载为只读；应用 SQLite、原文缓存、日志、输出和备份按实际资料范围设置访问权限。有客户隔离、跨部门或外部访问需求时，须另行建设登录、用户与文档授权、来源 ACL 映射。

接入前用独立目录验证真实表头映射、每案案号命名、转换器和扫描PDF比例。工作台新增根分类选择“私密资料”，首次同步检查冲突/失败和核实交局依据。不应把未知自动置成已完成。没有OCR的扫描件需本机OCR流程或人工录入有据字段。

应用级外发保护不能替代网络出口限制。切换生成或检索模型后，应重新核对同一组业务任务的输出与资源消耗。

## 恢复

代码从本仓库的目标提交恢复。初始化脚本生成的旧 `.env` 与应用库备份位于本机 `data/scenario-backups/<时间>/`；停止服务后在独立目录核对，再按需恢复配置。Git 不跟踪运行数据库和索引，代码回退不会自动恢复它们。

运行资料需分别备份 MySQL、SQLite、共享盘原件、索引及模型版本，并在隔离环境验证恢复结果。
