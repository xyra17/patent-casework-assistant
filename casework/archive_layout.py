"""Physical archive names shared by migration and browsing."""
import re
from casework import domains

DOMAIN_DIRECTORIES = {
    '人工智能': '01_AI领域',
    '无线通信': '02_无线通信领域',
    '光通信': '03_光通信领域',
    '机械结构': '04_机械结构领域',
    '汽车技术': '05_汽车技术领域',
    '终端软件': '06_终端软件领域',
}
CASE_DIRECTORIES = (
    '01_立案资料', '02_技术交底', '03_沟通记录', '04_申请文件', '05_办理文件',
    '02_技术交底/附图及补充实验材料', '05_办理文件/后续通知及答复材料',
)


def case_year(case_id):
    match = re.fullmatch(r'(?:[A-Za-z]+-)+(\d{4})-\d+', case_id or '')
    return match[1] if match else '年份待核实'


def domain_directories(values=()):
    result = dict(DOMAIN_DIRECTORIES)
    for name in domains.options(values):
        if name not in result:
            if re.search(r'[/\\\x00]|^\.+$', name):
                raise ValueError('领域名称不能包含路径字符')
            result[name] = f'{len(result)+1:02d}_{name}领域'
    return result
