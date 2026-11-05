"""Coordinate local models sharing the process-wide Metal command stream."""
from contextlib import contextmanager
import threading

_MPS_LOCK = threading.RLock()


@contextmanager
def inference_guard(device):
    if str(device).split(':', 1)[0] != 'mps':
        yield
        return
    import torch
    with _MPS_LOCK:
        try:
            yield
        finally:
            # A model-local lock does not protect another model's queued commands.
            torch.mps.synchronize()
