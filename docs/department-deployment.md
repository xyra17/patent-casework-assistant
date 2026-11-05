# 部门共享盘场景部署与恢复

## 本机独立模拟环境

在代码目录准备依赖、运行本地MySQL、配置本地 `.env` 中的只读及开发root连接和本机BGE模型路径。

1. `PYTHONPATH=. .venv/bin/python scripts/build_department_demo.py` 生成独立模拟共享盘；已有文件默认不覆盖，`--force`仅用于明确重建模拟资料。
2. `PYTHONPATH=. .venv/bin/python scripts/setup_department_demo.py --activate` 建立独立department_workbench、department_management、最小权限账号和SQLite，备份旧配置后切换环境，解析并镜像。
3. 公司内部手册、制度放入 `知识库文件/90_公司文档`，运行 `.venv/bin/python scripts/build_knowledge_base.py` 发布公司文档索引。案件原件由casework索引管理；两者都属于内部知识库。初始化不自动导入公开演示参考。
4. `cd frontend && npm ci && npm run build`；回代码目录运行 `.venv/bin/python main.py`。

前端更新会保留 `frontend/dist/assets` 中已有的带哈希资源，供更新前已打开的页面继续加载执行详情、执行记录等异步组件。在线更新时不要先删除 `dist`，也不要使用会删除旧资源的同步选项；旧资源只在维护窗口、确认旧页面均已关闭或刷新后清理。若页面已经遇到旧资源 404，保留未提交内容后刷新页面即可取得新入口。
5. 打开首页“案件”，依次核对同步、字段冲突、相似案件、会议、月报与推荐。自动同步默认300秒；即时检查可点“立即同步”。

脚本只支持localhost开发MySQL，创建隔离库，不覆盖pharma_db或patent_workbench。它会创建新角色；重复激活会产生新的最小权限角色，旧角色由管理员核对后清理，不应在生产反复运行开发初始化脚本。账号仅写本机.env（600权限），不输出密码。

## 真实部门内网

先将应用、依赖和Embedding/生成模型部署在获准内网；离线准备模型和转换器，不在接入私密材料后自动下载。

| 配置 | 含义 |
| --- | --- |
| DATA_MODE=intranet | 强制启用外发阻断 |
| DEEPSEEK_BASE_URL | 内网OpenAI兼容生成模型端点；环境变量保留旧名称，不限定供应商 |
| DEEPSEEK_MODEL / DEEPSEEK_API_KEY | 内网模型名与其访问凭据 |
| LOCAL_MODEL_HOSTS | 明确批准的内网推理主机名，逗号分隔；不要填公网主机 |
| EMBEDDING_MODEL_PATH / CASEWORK_EMBEDDING_MODEL_PATH | 本机已存在的BGE模型目录 |
| CASEWORK_DB_PATH | SQLite目录、解析历史、确认与审计文件 |
| CASEWORK_ALLOWED_ROOTS | 获准共享盘挂载目录的JSON数组；不要允许整个home或磁盘根 |
| CASEWORK_SYNC_INTERVAL | 周期扫描秒数，0关闭；实际执行限制30–86400 |
| CASEWORK_MYSQL_MIRROR | 1启用结构化投影；0只用本地工作台并明确镜像停用 |
| MYSQL_CASEWORK_SYNC_USER / PASSWORD | 独立同步账号，只对cw表有CRUD，不能交给Agent |

共享盘挂载为只读；应用SQLite、原文缓存、日志、输出和备份也包含敏感信息，需要内网文件权限和备份政策。部门全员共享范围符合本轮用户描述；有客户隔离、其他部门或外部访问时，必须先建设登录、用户/文档授权和源ACL映射，本轮未实现。

接入前用独立目录验证真实表头映射、每案案号命名、转换器和扫描PDF比例。工作台新增根分类选择“私密资料”，首次同步检查冲突/失败和核实交局依据。不应把未知自动置成已完成。没有OCR的扫描件需本机OCR流程或人工录入有据字段。

应用级外发保护不能替代防火墙。共享盘、本地模型、业务库、日志与备份出口应在基础设施层限制。当前Cloud demo验收不证明所选本地模型质量，切换本地模型后必须重跑同题和未见题。

## 恢复

代码恢复：项目根下 `专利场景改造前/专利场景改造前_完整代码文档_20261026.zip` 内恢复说明指导解压源码或从bundle恢复Git，核对校验清单；原始基线分支 `baseline/pre-patent`、标签 `pre-patent-baseline-20261025`。在独立空目录恢复，不覆盖当前工作区。

运行环境恢复：停止服务，私下将所需 `data/scenario-backups/<时间>/previous.env` 恢复到.env并保留600权限；原库与旧索引目录未删，配置指向即可。SQLite快照用单独目录验证后恢复。不要只git切分支就以为运行数据库和索引同时回滚；Git不跟踪这些运行数据。

全量灾备还须对MySQL做一致性备份、SQLite在线backup、共享盘与本地模型资产版本清单。改造前压缩包的范围是代码和文档，不是含真实数据的生产灾备。

## 复验命令
