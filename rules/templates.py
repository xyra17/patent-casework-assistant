"""Editable department conventions; examples are disabled until the user enables them."""
TEMPLATES = [
    {'key': 'patent-jargon', 'title': '专利业务黑话与简称', 'category': 'term', 'subject': '专利代理业务内部简称',
     'content': '所限＝本项目的初稿期限；客户期限／客户送审期限＝送客户或客户IPR审核的计划期限；提交期限＝向专利局提交的计划期限。\n'
                '交局＝向专利局提交材料，不表示已经授权。交底＝发明人提供的技术交底材料。\n'
                '客户IPR＝客户侧的知识产权对接人；三方会＝客户IPR、代理师、发明人参与的技术沟通会议，实际参会人以会议记录为准。\n'
                'Boss＝本项目负责确认分案的部门决策者。所号／所编号／我方案号＝代理部门内部案件编号，客户案号另列。\n'
                '初稿、客户确认稿、提交稿是不同阶段的文件版本；文件名未标阶段时，不自行判定为提交稿。\n'
                '用户另行解释简称时沿用用户定义；未收录或有歧义的黑话先询问具体含义。',
     'match_mode': 'keywords', 'match_terms': ['所限', '客户期限', '交局', '交底', 'IPR', '三方会', 'Boss', '所号'],
     'aliases': ['所编号', '我方案号', '客户送审期限', '提交期限', '初稿', '客户确认稿', '提交稿'],
     'scope_note': '本项目专利代理部门的案件查询、会议、文件对比及分案；不将这些内部简称推广为其他行业或机构的统一定义。',
     'binding': None, 'active': False},
    {'key': 'answer-order', 'title': '日常查询先给结论', 'category': 'preference', 'subject': '日常查询结果结构',
     'content': '案件查询先给直接结论，再列必要依据和来源；用户要求详细说明时展开。',
     'match_mode': 'keywords', 'match_terms': ['案件查询', '案件日期', '初稿期限'], 'aliases': ['查案件'],
     'scope_note': '案件信息、日期和期限的日常查询；不适用于正式报告。', 'binding': None, 'active': False},
    {'key': 'comparison-table', 'title': '技术对比按项目列示', 'category': 'preference', 'subject': '技术对比结果结构',
     'content': '技术比较优先按比较项、方案原文、对照原文、已证实差异和未知项列示，保留两侧引用。',
     'match_mode': 'keywords', 'match_terms': ['技术对比', '方案差异'], 'aliases': ['技术比较', '对照分析'],
     'scope_note': '用户要求比较技术方案时；不改变用户明确指定的输出格式。', 'binding': None, 'active': False},
    {'key': 'meeting-list', 'title': '会议结果按日期整理', 'category': 'preference', 'subject': '会议查询结果结构',
     'content': '会议查询按日期从近到远列出案号、实际参会人、主要内容和登记待补材料；保留每场来源。',
     'match_mode': 'keywords', 'match_terms': ['会议记录', '三方会议', '历史会议'], 'aliases': ['会议查询', '回顾会议'],
     'scope_note': '查询和回顾已有会议记录时；会议计划与未来安排不适用。', 'binding': None, 'active': False},
    {'key': 'case-number', 'title': '所号统一称呼', 'category': 'term', 'subject': '案件所号',
     'content': '本项目中的“所号”“所编号”“我方案号”指代理部门内部案件编号；与客户案号分别保留。',
     'match_mode': 'keywords', 'match_terms': ['所号', '所编号', '我方案号'], 'aliases': [],
     'scope_note': '本项目案件信息查询与文件关联，不将其他系统编号自动视为所号。', 'binding': None, 'active': False},
]
