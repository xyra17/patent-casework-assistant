"""Recognize unfilled templates without rewriting ordinary business statuses."""
import re

PATTERNS = [
    re.compile(r'\{S[1-9]\d*\s+(?:facts_token|quote_token)\}'),
    re.compile(r"\{\{[^{}]{0,120}\}\}|\$\{[^{}]{0,120}\}"),
    re.compile(r"\[待[^\]]{0,80}\]"),
    re.compile(r"TODO(?:[ \t]+待补充)?|TBD|XXX|等待子任务|等待任务|（略）|\(略\)", re.I),
    re.compile(r"[（(\[](?:此处)?占位(?:内容|文本)?[）)\]]|(?m:^[ \t]*(?:[-*][ \t]+)?占位(?:符|内容|文本)?[。.]?[ \t]*$)"),
    re.compile(r"(?m)^(?:[ \t]*[-*][ \t]+)?[ \t]*(?:待补充|待填写)[ \t]*[。.]?[ \t]*$"),
]


def has_placeholders(text):
    # Unresolved evidence selectors have their own fail-closed validator.
    text = re.sub(r'\{\{(?:quote|facts):[^{}]*\}\}', '', text or '')
    return any(pattern.search(text) for pattern in PATTERNS)


def replace_placeholders(text):
    for pattern in PATTERNS:
        text = pattern.sub("（未获取到该项内容）", text)
    return text
