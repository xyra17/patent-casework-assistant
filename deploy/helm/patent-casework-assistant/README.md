# Patent Casework Assistant Helm Chart

当前部门共享盘部署另见[内网与恢复说明](../../../docs/department-deployment.md)。旧通用初始化不会自动创建部门投影库，真实共享盘必须只读挂载并使用内网模型，模板存在不代表该场景已生产验收。

```bash
helm lint deploy/helm/patent-casework-assistant
helm template patent-casework-assistant deploy/helm/patent-casework-assistant \
  --set ingress.enabled=true \
  --set ingress.host=patent-casework-assistant.example.com \
  --set image.repository=registry.example.com/patent-casework-assistant \
  --set image.tag=1.0.0 > /tmp/patent-casework-assistant.yaml
```

生产前必须：

1. 准备外部 MySQL，并设置 `config.MYSQL_*` / `secret.MYSQL_PASSWORD`；
2. 准备 `knowledgeBase.existingClaim`（或接受空知识库演示）；
3. 配置 `secret.DEEPSEEK_API_KEY`、`secret.TAVILY_API_KEY`；
4. 配置 Ingress host、TLS secret / cert-manager；
5. 保持单副本部署，外置状态后再考虑多副本。

默认 `replicaCount: 1`，chart 会拒绝多副本，因为 SQLite/Chroma/本地文件仍是单写者状态。

## 2026-10-23 检索升级维护

升级后先更新索引，确认 schema_version=2、归一化向量与词法侧文件同时发布。KNOWLEDGE_MAX_HOPS 默认 3，KNOWLEDGE_SEARCH_LIMIT 默认每轮 6 次，覆盖检查超时 20 秒；阈值须按实际语料校准。没有新增外部检索服务或模型下载要求。

本地 Chroma 锁只覆盖单进程，多 worker/多副本共享目录的发布一致性未由本次变更解决。管理入口尚无完整身份授权，开发模板不等于公网生产验收。
