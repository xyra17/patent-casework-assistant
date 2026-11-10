# 本地演示配置

按根目录README启动MySQL并首次运行`setup_department_demo.py --activate`。该脚本创建分离的查询、同步、导入与维护账号；先配置本地root密码，不要赋予Agent root权限。

已有库不要重复初始化；保留`data/scenario-backups/`中的配置和应用库备份。生成资料是34案独立模拟输入，不是本机历史评测数据的恢复。

默认`CASEWORK_EMBEDDING=0`便于不下载权重运行核心查询。需要向量检索时准备本地权重并开启，需重新同步案件资料。公司文档从空目录开始，由使用者明确接入。

前端生产包由`npm --prefix frontend run build`生成，FastAPI服务入口为`main.py`。运行文件、上传和数据库位于Git忽略的本地目录。
