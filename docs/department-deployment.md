# 本地运行与数据接入

按根目录 README 安装依赖、配置模型与本地 MySQL 后，首次初始化模拟场景：

```bash
PYTHONPATH=. .venv/bin/python scripts/setup_department_demo.py --activate
.venv/bin/python scripts/build_knowledge_base.py
.venv/bin/python main.py
```

初始化脚本仅接受本机开发 MySQL，创建独立演示数据库和账号，并备份原配置。仓库已包含模拟资料，无需重新生成。

启动后打开 `http://localhost:8000`。在“案件”页面查看案件、会议、期限和月报；在会话中提问或上传文件。

## 接入自己的资料

通过案件页面添加获准目录，并在 `.env` 的 `CASEWORK_ALLOWED_ROOTS` 中配置对应路径。案件文件与公司文档分别管理：案件目录执行同步，公司文档上传后更新索引。

接入私密资料时，设置 `DATA_MODE=intranet`，使用本地或获准内网模型，原件目录只读挂载。当前项目以本地演示为主；对外部署需另行配置身份和访问权限。

## 更新与备份

更新前备份 `.env`、MySQL、应用 SQLite、原件和索引。代码回退不会恢复运行数据；初始化备份保存在本机 `data/scenario-backups/`。

更新前端时保留已有 `frontend/dist/assets`，避免已打开的页面丢失异步组件；旧资源可在维护窗口统一清理。
