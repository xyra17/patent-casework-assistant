"""Shared domain vocabulary. Unknown source values remain visible, never guessed."""
import re
import unicodedata

DOMAINS = {
    '人工智能': ('AI', '人工智能'),
    '无线通信': ('无线', '无线通信'),
    '光通信': ('光', '光通信'),
    '机械结构': ('结构', '机械结构', '机械'),
    '汽车技术': ('车', '汽车', '汽车技术', '车辆技术'),
    '终端软件': ('终端软件', '终端应用软件'),
}


def key(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value))).casefold()


def canonical(value):
    if value is None:return None
    text=str(value).strip()
    return next((name for name,aliases in DOMAINS.items() if key(text) in {key(a) for a in aliases}),text)


def options(values=()):
    return list(DOMAINS)+sorted({canonical(v) for v in values if v and canonical(v) not in DOMAINS})


def mentioned(text, value):
    text=key(text);name=canonical(value)
    for alias in DOMAINS.get(name,(name,)):
        alias=key(alias)
        if alias=='ai':
            if re.search(r'(?<![a-z0-9])ai(?![a-z0-9])',text):return True
        elif alias in {'车','光','结构','机械'}:
            if re.search(re.escape(alias)+r'(?:领域|类案件|方向|技术)',text):return True
        elif alias and alias in text:return True
    return False
