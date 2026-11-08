# Helm 部署模板

模板使用单副本，需配置外部 MySQL、模型接口和持久化存储。当前 SQLite、Chroma 与本地文件由同一实例管理。

```bash
helm lint deploy/helm/patent-casework-assistant
helm template patent-casework-assistant deploy/helm/patent-casework-assistant \
  --set ingress.enabled=true \
  --set ingress.host=patent-casework-assistant.example.com \
  --set image.repository=registry.example.com/patent-casework-assistant \
  --set image.tag=1.0.0 > /tmp/patent-casework-assistant.yaml
```

模型凭据通过 Secret 配置；接入私密资料时使用获准内网模型，并将原件只读挂载。[本地运行与数据接入](../../../docs/department-deployment.md)。
