"""Uniform case folders projected over the published source catalog."""
from collections import defaultdict
from pathlib import PurePosixPath
import re
from casework import catalog, store
from casework.archive_layout import case_year, domain_directories
from casework.domains import canonical

FOLDERS = (
    ('intake', '立案资料'), ('disclosure', '技术交底'), ('communication', '沟通记录'),
    ('application', '申请文件'), ('prosecution', '办理文件'), ('other', '其他资料'),
)
KIND_FOLDER = {'intake': 'intake', 'ledger': 'intake', 'disclosure': 'disclosure',
               'drawing': 'disclosure', 'meeting': 'communication', 'application': 'application',
               'receipt': 'prosecution', 'filing': 'prosecution', 'fee': 'prosecution'}


def folder_key(document):
    path = PurePosixPath(document['relative_path'])
    # Honor an explicit source folder; the case folder/title is not a file type.
    for part in reversed(path.parts[1:-1]):
        label = re.sub(r'^\d+[_、.\s-]*', '', part)
        match = next((key for key, name in FOLDERS if label == name), None)
        if match:
            return match
    name = path.name
    if re.search('通知书|审查意见|答复|补正|回执|交局确认|提交确认|缴费|费用', name):
        return 'prosecution'
    return KIND_FOLDER.get(document.get('kind'), 'other')


def grouped(documents):
    groups = {key: {'key': key, 'label': label, 'count': 0, 'documents': []} for key, label in FOLDERS}
    for original in documents:
        document = {**original, 'name': PurePosixPath(original['relative_path']).name}
        group = groups[folder_key(document)]
        group['documents'].append(document)
        group['count'] += 1
    return list(groups.values())


@store.consistent_read
def overview(query='', offset=0, limit=20, domain='', year=''):
    cases = store.list_cases()
    keys = {case['case_key'] for case in cases}
    documents = catalog.documents()
    counts = defaultdict(lambda: defaultdict(int))
    unassigned = 0
    for document in documents:
        key = document['root_id'] + ':' + (document['case_id'] or '')
        if key not in keys:
            unassigned += 1
        else:
            counts[key][folder_key(document)] += 1
    terms = query.strip().casefold()
    directories = domain_directories(c.get('technical_domain') for c in cases)
    navigation = []
    for name, directory in directories.items():
        members = [c for c in cases if canonical(c.get('technical_domain')) == name]
        years = sorted({case_year(c['case_id']) for c in members}, reverse=True)
        navigation.append({'domain': name, 'directory': directory, 'count': len(members),
                           'years': [{'year': y, 'count': sum(case_year(c['case_id']) == y for c in members)} for y in years]})
    matches = [case for case in cases if (not domain or canonical(case.get('technical_domain')) == canonical(domain))
               and (not year or case_year(case['case_id']) == year)
               and (not terms or terms in ' '.join(str(case.get(k) or '') for k in
               ('case_id', 'title', 'client_case_id', 'client_alias', 'owner_name')).casefold())]
    rows = []
    for case in matches[offset:offset + limit]:
        folders = [{'key': key, 'label': label, 'count': counts[case['case_key']][key]} for key, label in FOLDERS]
        rows.append({**{key: case.get(key) for key in ('case_key', 'case_id', 'title', 'owner_name', 'client_alias', 'technical_domain')},
                     'year': case_year(case['case_id']),
                     'folders': folders, 'file_count': sum(item['count'] for item in folders)})
    return {'rows': rows, 'total': len(matches), 'offset': offset, 'limit': limit,
            'unassigned_count': unassigned, 'file_count': len(documents), 'case_count': len(cases), 'domains': navigation}


@store.consistent_read
def detail(case_key):
    case = store.get_case(case_key)
    if not case:
        raise KeyError('案件不存在或目录已停用')
    return {'case': {**{key: case.get(key) for key in ('case_key', 'case_id', 'title', 'owner_name', 'client_alias', 'technical_domain')},
                     'year': case_year(case['case_id'])},
            'folders': grouped(catalog.documents(case_key))}


@store.consistent_read
def unassigned():
    keys = {case['case_key'] for case in store.list_cases()}
    return {'documents': [doc for doc in catalog.documents()
                         if doc['root_id'] + ':' + (doc['case_id'] or '') not in keys]}


@store.consistent_read
def knowledge_status(company_status):
    """Company documents may be empty while the local case archive is usable."""
    documents = catalog.documents()
    parsed = sum(d['state'] == 'parsed' for d in documents)
    if not parsed:
        return {**company_status, 'case_documents': 0, 'company_documents': company_status}
    roots = store.roots(enabled=True)
    issues = []
    for root in roots:
        if root['state'] in ('ready', 'scanning'):
            continue
        reason = {
            'needs_confirmation': '源目录大量文件缺失，待核对；已保留原有解析记录',
            'needs_rescan': '目录需要重新扫描',
            'partial': '部分文件尚未成功解析，请查看目录同步记录',
            'error': '目录同步失败，请查看目录同步记录',
        }.get(root['state'], '目录尚未完成同步')
        issues.append({'root_id': root['id'], 'label': root['label'], 'state': root['state'], 'reason': reason})
    if company_status['state'] == 'error':
        issues.append({'state': 'error', 'reason': '公司文档索引不可用：' + company_status.get('detail', '请检查索引')})
    detail = f'{len(store.list_cases())} 件案件 · {parsed} 份已解析案卷文件'
    if issues:
        detail += '；' + '；'.join(dict.fromkeys(issue['reason'] for issue in issues))
    elif company_status['state'] == 'empty':
        detail += '；公司文档未接入'
    return {'ok': not issues, 'state': 'partial' if issues else 'ok',
            'label': '部分可用' if issues else '已就绪', 'detail': detail,
            'issues': issues, 'case_documents': parsed, 'company_documents': company_status}
