"""Provider request options must be explicit and backward compatible."""
import os
import unittest
from unittest.mock import patch

from agent import llm


class LLMConfigurationTests(unittest.TestCase):
    def test_official_flash_uses_standard_sdk_without_relay_overrides(self):
        with patch('casework.privacy.ensure_model_allowed'), patch.object(llm, 'init_chat_model') as init, \
                patch.dict(os.environ, {'DEEPSEEK_MODEL': 'deepseek-flash', 'DEEPSEEK_BASE_URL': 'https://api.deepseek.com'}):
            llm.get_llm()
            options = init.call_args.kwargs
            self.assertEqual(options['model'], 'deepseek-flash')
            self.assertEqual(options['base_url'], 'https://api.deepseek.com')
            self.assertEqual(options['model_provider'], 'openai')
            self.assertNotIn('default_headers', options)
            self.assertNotIn('http_client', options)
            self.assertNotIn('http_async_client', options)

    def test_streaming_is_opt_in_and_validated_before_provider_call(self):
        with patch('casework.privacy.ensure_model_allowed'), patch.object(llm, 'init_chat_model') as init:
            for value, expected in [('', None), ('1', True), ('true', True), ('0', False), ('false', False)]:
                with self.subTest(value=value), patch.dict(os.environ, {'LLM_STREAMING': value}):
                    llm.get_llm()
                    if expected is None:
                        self.assertNotIn('streaming', init.call_args.kwargs)
                    else:
                        self.assertIs(init.call_args.kwargs['streaming'], expected)
            before = init.call_count
            with patch.dict(os.environ, {'LLM_STREAMING': 'invalid'}), self.assertRaisesRegex(ValueError, 'LLM_STREAMING'):
                llm.get_llm()
            self.assertEqual(init.call_count, before)

    def test_thinking_option_is_explicit_and_invalid_values_fail_before_call(self):
        with patch('casework.privacy.ensure_model_allowed'), patch.object(llm, 'init_chat_model') as init:
            for mode in ('', 'enabled', 'disabled'):
                with self.subTest(mode=mode), patch.dict(os.environ, {'LLM_THINKING': mode}):
                    llm.get_llm()
                    options = init.call_args.kwargs
                    if mode:
                        self.assertEqual(options['extra_body'], {'thinking': {'type': mode}})
                    else:
                        self.assertNotIn('extra_body', options)
            before = init.call_count
            with patch.dict(os.environ, {'LLM_THINKING': 'invalid'}), self.assertRaises(ValueError):
                llm.get_llm()
            self.assertEqual(init.call_count, before)
            with patch.dict(os.environ, {'LLM_THINKING': '', 'LLM_REASONING_EFFORT': 'low',
                                         'LLM_MAX_TOKENS': '8192'}):
                llm.get_llm()
                self.assertEqual(init.call_args.kwargs['reasoning_effort'], 'low')
                self.assertEqual(init.call_args.kwargs['max_tokens'], 8192)
