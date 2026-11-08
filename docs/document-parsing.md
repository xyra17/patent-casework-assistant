# 文档解析

| 文件 | 处理方式 |
| --- | --- |
| Word | 提取段落和表格；旧 DOC 需要本机 LibreOffice 或 textutil |
| Excel / CSV | 读取表格，使用程序筛选和计算 |
| 文本 PDF | 逐页提取文字 |
| 扫描 PDF、图片和复杂版面 | 使用本地 MinerU 提取文字与表格 |
| PDF 附图 | 使用支持视觉输入的模型观察指定页面 |

## 安装本地解析器

```bash
bash scripts/setup_mineru.sh
```

在 `.env` 中配置：

```dotenv
DOCUMENT_PDF_PARSER=auto
MINERU_PYTHON=./.venv-mineru/bin/python
MINERU_TIER=basic
MINERU_TIMEOUT=300
```

## 使用

在会话中上传文件并描述需求。公司文档上传后需更新索引，案件文件需同步接入目录，才会进入检索。

扫描件解析在本机执行；图片理解需要已配置的模型支持视觉输入。接入私密资料时使用获准的本地或内网模型。看图结果与原文提取分别保留，不能互相替代。
