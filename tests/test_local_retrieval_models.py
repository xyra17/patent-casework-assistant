"""Preserve source layout, query instructions and explicit truncation coverage."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

from utils.embedding_pool import SharedEmbedding, embedding_identity


class LocalRetrievalModelTests(unittest.TestCase):
    def test_blank_case_model_path_falls_back_to_shared_local_model(self):
        from casework.chunking import tokenizer_path
        from casework.search import embedding_config
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {'CASEWORK_EMBEDDING':'1','CASEWORK_EMBEDDING_MODEL_PATH':'',
                                         'EMBEDDING_MODEL_PATH':temporary}):
                self.assertEqual(tokenizer_path(), str(Path(temporary).resolve()))
                self.assertEqual(embedding_config()[0], Path(temporary).resolve())

    def test_embedding_preserves_newlines_and_query_prompt(self):
        calls = []
        def encode(texts, **kwargs):
            calls.append((texts, kwargs))
            return np.asarray([[1., 0.] for _ in texts])
        model = SimpleNamespace(_client=SimpleNamespace(encode=encode),
                                encode_kwargs={'normalize_embeddings': True},
                                query_encode_kwargs={'prompt': 'Instruct: task\nQuery: '})
        embedding = SharedEmbedding(model)
        embedding.embed_documents(['标题\n技术段落'])
        embedding.embed_query('第一项\n第二项')
        self.assertEqual(calls[0][0], ['标题\n技术段落'])
        self.assertEqual(calls[1][0], ['第一项\n第二项'])
        self.assertEqual(calls[1][1]['prompt'], 'Instruct: task\nQuery: ')

    def test_index_identity_changes_when_query_instruction_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary)/'config.json').write_text('{}')
            with patch.dict(os.environ, {'EMBEDDING_MODEL_PATH': temporary, 'RETRIEVAL_QUERY_INSTRUCTION': 'first'}):
                first = embedding_identity()
            with patch.dict(os.environ, {'EMBEDDING_MODEL_PATH': temporary, 'RETRIEVAL_QUERY_INSTRUCTION': 'second'}):
                self.assertNotEqual(first, embedding_identity())

    def test_qwen_window_offsets_and_query_shortening_are_explicit(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import PreTrainedTokenizerFast
        from casework.local_reranker import qwen_pair_windows
        backend = Tokenizer(models.WordLevel({'[UNK]': 0, '[PAD]': 1, 'alpha': 2, 'beta': 3}, unk_token='[UNK]'))
        backend.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token='[UNK]', pad_token='[PAD]')
        text = 'alpha beta ' * 150
        windows, detail = qwen_pair_windows(tokenizer, 'alpha', text, maximum=512)
        self.assertTrue(detail['complete_passage'])
        self.assertEqual(windows[-1][1][1], len(text.rstrip()))
        self.assertTrue(all(len(values['input_ids']) <= 512 for values, _ in windows))
        _, detail = qwen_pair_windows(tokenizer, 'alpha ' * 200, text * 10, maximum=512)
        self.assertTrue(detail['query_shortened'])
        self.assertFalse(detail['complete_passage'])
        self.assertEqual(detail['windows'], 4)


if __name__ == '__main__':
    unittest.main()
