import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from utils.local_inference import inference_guard


def test_mps_models_share_guard_and_finish_commands_before_handoff():
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    calls = []

    def first():
        with inference_guard('mps'):
            calls.append('first')
            entered.set()
            assert release.wait(2)

    def second():
        assert entered.wait(2)
        with inference_guard('mps:0'):
            calls.append('second')
            second_entered.set()

    with patch('torch.mps.synchronize', side_effect=lambda: calls.append('sync')):
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(first)
            b = pool.submit(second)
            assert entered.wait(2)
            assert not second_entered.wait(.03)
            release.set()
            a.result(timeout=2)
            b.result(timeout=2)
    assert calls == ['first', 'sync', 'second', 'sync']


def test_failed_mps_work_is_synchronized_and_does_not_block_cpu():
    with patch('torch.mps.synchronize') as sync:
        try:
            with inference_guard('mps'):
                raise ValueError('failed work')
        except ValueError:
            pass
        with inference_guard('cpu'):
            pass
        sync.assert_called_once()
