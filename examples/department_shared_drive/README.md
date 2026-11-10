# 可生成的模拟业务资料

本目录只提交字段口径；34个模拟案件及预期记录由代码生成，不提交重复的Excel、Word、数据库或运行结果。

```bash
PYTHONPATH=. .venv/bin/python scripts/build_department_demo.py
```

输出为本目录的`source/`与`expected.json`，均被Git忽略。人物、客户和案号均为独立编写的模拟数据，不代表真实客户、历史业务合同或专业判断准确率。

已有资料时脚本拒绝覆盖。首次运行`setup_department_demo.py --activate`和离线测试入口会在资料尚未生成时自动生成；不要对已有业务目录运行强制覆盖。
