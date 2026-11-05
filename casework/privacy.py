"""Deny outbound model/search requests before any confidential content is sent."""
import ipaddress
import os
from urllib.parse import urlsplit


def local_endpoint(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http','https') or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or '').lower()
    if host == 'localhost':
        return True
    try:
        value = ipaddress.ip_address(host)
        return value.is_loopback or value in ipaddress.ip_network('10.0.0.0/8') or value in ipaddress.ip_network('172.16.0.0/12') or value in ipaddress.ip_network('192.168.0.0/16')
    except ValueError:
        # Administrators may explicitly approve their intranet inference hostname.
        return host in {h.strip().lower() for h in os.getenv('LOCAL_MODEL_HOSTS','').split(',') if h.strip()}


def protected():
    from casework.store import has_private_material
    return os.getenv('DATA_MODE','demo') == 'intranet' or has_private_material()


def ensure_model_allowed():
    if protected() and not local_endpoint(os.getenv('DEEPSEEK_BASE_URL','https://api.deepseek.com')):
        raise PermissionError('已启用内网模式或接入仅限本地资料：禁止调用外部模型。请配置本地OpenAI兼容推理端点后重试。')


def ensure_web_allowed():
    if protected():
        raise PermissionError('内网保密模式禁止将问题、摘要、客户或IPR信息发送到外部检索服务。')
