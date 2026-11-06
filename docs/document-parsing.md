# 文档解析与视觉理解

## 当前链路

- Word：python-docx；旧DOC由本机LibreOffice/textutil转换。
- Excel/CSV：pandas、openpyxl/xlrd，表格筛选与聚合仍由程序执行。
- 普通文字PDF：pypdf逐页提取。
- 扫描PDF、包含图像/分栏的PDF及单页图片：本地MinerU 4.1.7。调用无状态Python SDK，默认全页，校验页码与完整文档标记。
- 图片、PDF 附图理解：`analyze_visual_document` 将选定页渲染成图像，调用当前配置且支持视觉输入的生成模型。每次最多 6 页，返回实际覆盖页码。

MinerU负责OCR、版面与表格文字；视觉模型负责回答部件、箭头、流程、图表含义等问题。两者均保留文件SHA256版本，视觉结果标记为观察与推断，不作为原文逐字引文。OCR正文不自动覆盖案件信息或会议事实。

## 本地安装

在项目目录运行 `bash scripts/setup_mineru.sh`。脚本建立`.venv-mineru`，通过只读搜索路径复用主环境已安装的ML运行库；MinerU专属依赖写入自己的环境，不修改主环境的软件包。

网络受限时可为pip配置镜像；模型资源下载源可设`MINERU_MODEL_SOURCE=modelscope`。首次下载只获取模型资源，文档解析在本机执行，不调用MinerU云端API。

```dotenv
DOCUMENT_PDF_PARSER=auto
MINERU_PYTHON=./.venv-mineru/bin/python
MINERU_TIER=basic
MINERU_TIMEOUT=300
```

- `auto`：根据PDF文字、图像资源与对齐版式分流。
- `mineru`：所有PDF走MinerU，适合自动检测未覆盖的复杂文件。
- `native`：仅允许普通文字PDF，疑似扫描或复杂版面明确报错。
- `basic`：本机OCR和版面模型；`standard/advanced`需要另外准备对应VLM模型和运行资源，不能仅改配置就声称验证通过。

缓存位于`data/document-parse-cache/`，按原件版本、文件类型和解析配置区分。失败日志保存在此目录，可能包含文档信息，不应公开分享。文件原件不会改写。

## 使用

会话中直接上传图片或PDF并描述需求。正文提取使用`read_file_content`，看图使用`analyze_visual_document`。案卷检索返回`file_id`和`revision`后，可用同一视觉工具读取原件指定页；它会检查来源权限、目录状态及原件版本。

公司文档上传图片后须点击“更新索引”；案件档案须同步接入目录，才会发布新的OCR检索正文。旧解析证据保留历史版本，不能把未更新的旧索引当作新解析已生效。

内网保密约束继续生效：MinerU 在本地运行；保密资料若指向外部模型端点，视觉调用会被拒绝。纯图形没有可识别文字时，OCR 可以失败；视觉工具的观察结果不能冒充 OCR 原文。

## 验证

`tests/test_multimodal_documents.py`覆盖混合PDF分流、全页完整性、表格定位、OCR不覆盖案件事实、图像输入、来源权限与视觉页码范围。
