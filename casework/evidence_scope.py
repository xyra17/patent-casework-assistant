"""Object/state-bound literal coverage; never a general semantic proof."""
import re

STATE = r'启用|停用|开启|关闭|执行|运行|生效|采用|使用|加密|发送|调整|调节|触发|切换|工作|控制'
REF = re.compile(r'^(?:而|但|且)?(?:该|上述|前述|所述|本)(?:功能|方法|装置|步骤|方案|操作|调节|过程|模块)')
NEG = r'(?:不|未|禁止|不得|无需)(?:再|继续|持续|会|被|予以|进行|可|能){0,3}'
PHASE = re.compile(r'(?:时|阶段|期间|条件下|模式下)')


def attributes(query):
    from casework.parser import CASE_RE
    clean=CASE_RE.sub('',query).strip(' 的：:')
    parts=re.split(r'是否|是不是|何时|什么时候|在什么条件|在何种条件|为什么|为何|如何|怎么',clean,maxsplit=1)
    head=parts[0] if len(parts)>1 else ''
    head=re.split(r'[，,。；;：:]|判断|核对|分析|说明|列出|查明|读取',head)[-1]
    head=re.split(r'在(?=启动|正常|运行|维护|故障|异常|什么|何种)',head)[0]
    head=re.sub(r'^(?:请|查找|查询|核对|说明|这个|该|本|上述)+','',head).strip(' 的：:')
    head=re.sub(r'(?:控制|运行|处理|启用|执行)$','',head)
    if head in ('方案','方法','功能','系统') or len(head)>24 or re.search(r'[，。？?]',head):head=''
    predicate=re.split(r'是否|是不是|何时|什么时候|为什么|为何|如何|怎么',clean,maxsplit=1)[-1]
    verbs = re.findall(STATE, predicate)
    if '调整' in verbs or '调节' in verbs:
        verbs.extend(['调整', '调节'])
    return {'actions': list(dict.fromkeys(verbs)), 'subject_phrase':head,
            'universal': bool(re.search(r'一直|始终|全程|总是|\balways\b', query, re.I))}


def assess(requirements, window, facets, end_pattern, reference_pattern):
    from casework.subject_support import contains, subjects
    text = window['text']
    terms = requirements['terms']
    actions = requirements.get('actions', [])
    verb = '|'.join(re.escape(a) for a in actions) or STATE
    missing_terms = [t for t in terms if not contains(text, t)]
    facts = []
    previous = False
    ambiguous = False
    # A comma is a scope boundary: a restriction on another noun must not
    # inherit the subject simply because it occurs in the same paragraph.
    for clause in re.split(r'[。！？；;!\n，,]+', text):
        clause = clause.strip()
        if not clause or '|' in clause:
            continue
        subject = requirements.get('subject_phrase')
        direct = contains(clause,subject) if subject else any(contains(clause, t) for t in terms)
        ref = bool(REF.match(clause)) and previous
        matches=list(re.finditer(verb,clause))
        action=matches[-1] if matches else None
        # Explicit anaphora can name the operation (e.g. "上述调节") while
        # its predicate describes its enabled/disabled state.
        ref_match = REF.match(clause)
        if ref and (action is None or action.start() < ref_match.end()):
            action = re.compile(STATE).search(clause, ref_match.end())
        if action is None and previous and PHASE.search(clause):
            action = re.compile(STATE).search(clause, list(PHASE.finditer(clause))[-1].end())
        if action is None and direct and re.search(r'仅|只在|只有', clause):
            action = re.search(r'控制|执行|工作', clause)
        if direct and action and PHASE.search(clause[:action.start()]):
            prefix=PHASE.split(clause[:action.start()])[-1]
            nouns=[t for t in subjects(prefix) if t not in {'阶段','状态','情况','模式','功能'}]
            if nouns and not (contains(prefix,subject) if subject else any(contains(prefix,t) for t in terms)):
                direct=False
        continuation = False
        condition_lead=bool(re.match(r'^(?:在|当|若|如果|否则|除非|正常|启动|故障|异常|运行|检修|维修|其余|其他|[^仅只在]{1,20}时)',clause))
        if previous and action and condition_lead and PHASE.search(clause[:action.start()]):
            prefix = PHASE.split(clause[:action.start()])[-1]
            nouns = [t for t in subjects(prefix) if t not in {'阶段', '状态', '情况', '模式', '功能'}]
            continuation = not nouns or any(contains(prefix, t) for t in terms)
        bound = bool((direct or ref or continuation) and (action or not actions))
        for name in requirements['facets']:
            cue = facets[name][1].search(clause)
            if not cue:
                continue
            negative = bool(action and re.search(NEG + '$', clause[:action.start()]))
            exclusive = bool(action and re.search(r'仅|只(?:有|在)?|唯有', clause[:action.start()]))
            universal = bool(action and re.search(r'一直|始终|全程|总是|\balways\b', clause[:action.end()], re.I))
            if not bound:
                ambiguous = True
                continue
            if name in ('conditions','exceptions') and action and not (negative or universal or facets[name][1].search(clause[:action.start()])):
                continue
            if name == 'conditions' and requirements.get('universal') and not (negative or exclusive or universal):
                continue
            fact = {'facet': name, 'quote': clause, 'negative': negative,
                    'exclusive': exclusive, 'universal': universal, 'binding': 'object_state_clause'}
            if fact not in facts:
                facts.append(fact)
        # Only an explicit operation establishes an antecedent. An unrelated
        # intervening sentence clears it; headers are handled as separate units.
        if direct and re.search(STATE, clause):
            previous = True
        elif bound:
            previous = True
        elif subjects(clause):
            previous = False
    # A simple parsed table is a separate structure read, not a text-window
    # merge. A unique object column plus same-row state is required.
    table_rows = []
    for block in re.findall(r'(?:^[^\n]*\|[^\n]*(?:\n|$))+', text, re.M):
        rows=[line.strip() for line in block.strip().splitlines()]
        table_rows.extend(rows)
        for i, line in enumerate(rows):
            cells = [c.strip() for c in line.strip('|').split('|')]
            subject=requirements.get('subject_phrase')
            columns = [j for j,c in enumerate(cells) if (contains(c,subject) if subject else any(contains(c,t) for t in terms))]
            if len(columns) != 1:
                continue
            column = columns[0]
            for row in rows[i+1:]:
                values = [c.strip() for c in row.strip('|').split('|')]
                if len(values) != len(cells) or not re.fullmatch('(?:' + NEG + ')?(?:' + verb + ')', values[column]):
                    break
                for facet in requirements['facets']:
                    if facet not in ('conditions','exceptions') or (facet=='exceptions' and not re.match(NEG,values[column])):continue
                    facts.append({'facet': facet, 'quote': '\n'.join(rows[i:]),
                                  'negative': bool(re.match(NEG, values[column])), 'exclusive': False,
                                  'universal': False, 'binding': 'object_column_state_row'})
    missing = []
    for name in requirements['facets']:
        supported = [f for f in facts if f['facet'] == name]
        if name == 'conditions' and requirements.get('universal'):
            supported = [f for f in supported if f['negative'] or f['exclusive'] or f['universal']]
        if not supported:
            missing.append(name)
    if terms and len(missing_terms) / len(terms) > .5:
        missing.append('query_subject')
    if not end_pattern.search(text) and not table_rows:
        missing.append('unfinished_sentence')
    if reference_pattern.match(text.lstrip()):
        missing.append('leading_reference')
    checked = bool(requirements['facets'] or terms)
    return {'status': ('unknown' if ambiguous else 'incomplete') if missing else 'covered_by_checks' if checked else 'unknown',
            'missing': missing, 'missing_terms': missing_terms, 'checked_facets': requirements['facets'],
            'bound_facts': facts, 'object_relation_uncertain': ambiguous,
            'semantic_verified': False}
