"""Reuse one normalized local embedding model per path across archive and KB readers."""
from __future__ import annotations
import threading
import json
import os
import hashlib
from pathlib import Path
from langchain_core.embeddings import Embeddings
from utils.local_inference import inference_guard

_LOCK = threading.RLock()
_MODELS = {}
QUERY_INSTRUCTION = 'Given a technical question, retrieve patent case passages describing the relevant technical mechanism and its conditions.'
PIPELINE_VERSION = 'verbatim-fp32-512-v1'


def embedding_identity():
    """Bind company index to model configuration, weights and query instruction."""
    from utils.path_utils import PROJECT_ROOT
    name = os.getenv('EMBEDDING_MODEL_PATH', '').strip() or os.getenv('EMBEDDING_MODEL', 'Qwen/Qwen3-Embedding-0.6B')
    path = Path(name).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    markers = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(path.glob('*'))
               if p.is_file() and p.suffix in ('.json', '.safetensors', '.bin', '.model')]
    value = [PIPELINE_VERSION, str(path.resolve()) if path.is_dir() else name, markers,
             os.getenv('RETRIEVAL_QUERY_INSTRUCTION', QUERY_INSTRUCTION)]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class SharedEmbedding(Embeddings):
    def __init__(self, model, device=''):
        self.model = model
        self.device = device or str(getattr(model._client, 'device', 'cpu'))
        self.lock = threading.RLock()

    def embed_documents(self, texts):
        with self.lock, inference_guard(self.device):
            return self.model._client.encode(texts, show_progress_bar=False,
                                             **self.model.encode_kwargs).tolist()

    def embed_query(self, text):
        with self.lock, inference_guard(self.device):
            options = self.model.query_encode_kwargs or self.model.encode_kwargs
            return self.model._client.encode([text], show_progress_bar=False, **options)[0].tolist()


def get_embedding(model_name: str, *, local_only: bool = True):
    path = Path(model_name).expanduser()
    key = str(path.resolve()) if path.is_dir() else model_name
    instruction = os.getenv('RETRIEVAL_QUERY_INSTRUCTION', QUERY_INSTRUCTION)
    device = os.getenv('EMBEDDING_DEVICE', '').strip()
    cache_key = (key, device, instruction)
    with _LOCK, inference_guard(device):
        if cache_key not in _MODELS:
            from langchain_huggingface import HuggingFaceEmbeddings
            try:
                qwen = json.loads((path / 'config.json').read_text()).get('model_type') == 'qwen3'
            except (OSError, ValueError):
                qwen = False
            import torch
            options = {'local_files_only': local_only,
                       'model_kwargs': {'torch_dtype': torch.float32, 'attn_implementation': 'sdpa'}}
            if device:
                if device not in ('cpu', 'mps', 'cuda'):
                    raise ValueError('嵌入设备须为cpu/mps/cuda')
                options['device'] = device
            encode = {'normalize_embeddings': True, 'batch_size': int(os.getenv('EMBEDDING_BATCH_SIZE', '4'))}
            query = {**encode, 'prompt': f'Instruct: {instruction}\nQuery: '} if qwen else encode
            model = HuggingFaceEmbeddings(model_name=key, model_kwargs=options,
                                          encode_kwargs=encode, query_encode_kwargs=query)
            model._client.max_seq_length = 512
            if qwen:
                model._client.tokenizer.padding_side = 'left'
            _MODELS[cache_key] = SharedEmbedding(model, device=device)
        return _MODELS[cache_key]
