"""LLM 初始化（TDD §4.2 / §2 / §12）。

通过 OpenAI 兼容协议调用 DeepSeek 官方 API，配置从环境变量读取。
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler

class _PrivacyGuard(BaseCallbackHandler):
    raise_error=True
    def on_chat_model_start(self,*args,**kwargs):
        from casework.privacy import ensure_model_allowed
        ensure_model_allowed()

_privacy_guard=_PrivacyGuard()

# 加载项目根目录的 .env（保证直接运行本脚本也能读到配置）
from utils.path_utils import PROJECT_ROOT as _PROJECT_ROOT
load_dotenv(_PROJECT_ROOT / ".env")


def get_llm(*, temperature=None):
    """初始化 LLM 实例（默认 deepseek-flash，OpenAI 兼容协议）。

    timeout / max_retries 按 TDD §13 设置，避免外部调用无限阻塞。
    """
    from casework.privacy import ensure_model_allowed
    ensure_model_allowed()
    thinking = os.getenv("LLM_THINKING", "").strip()
    if thinking not in ("", "enabled", "disabled"):
        raise ValueError("LLM_THINKING must be enabled, disabled, or unset")
    effort = os.getenv("LLM_REASONING_EFFORT", "").strip()
    max_tokens = os.getenv("LLM_MAX_TOKENS", "").strip()
    if max_tokens and int(max_tokens) < 1:
        raise ValueError("LLM_MAX_TOKENS must be positive")
    streaming = os.getenv('LLM_STREAMING', '').strip().lower()
    if streaming not in ('', '0', '1', 'false', 'true'):
        raise ValueError('LLM_STREAMING must be 0, 1, false, true, or unset')
    from api.telemetry import model_telemetry
    return init_chat_model(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        model_provider="openai",
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        callbacks=[_privacy_guard, model_telemetry],
        **({'streaming': streaming in ('1', 'true')} if streaming else {}),
        **({'extra_body': {'thinking': {'type': thinking}}} if thinking else {}),
        **({'reasoning_effort': effort} if effort else {}),
        **({'max_tokens': int(max_tokens)} if max_tokens else {}),
        **({'temperature':temperature} if temperature is not None else {}),
        timeout=float(os.getenv("LLM_TIMEOUT", "30")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),
    )


if __name__ == "__main__":
    llm = get_llm()
    print("LLM 初始化成功")
    print("类型:", type(llm).__name__)
    print("模型:", getattr(llm, "model_name", None))
    print("端点:", getattr(llm, "openai_api_base", None))
