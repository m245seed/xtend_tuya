import importlib.util
import time
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "xtend_tuya"
    / "multi_manager"
    / "shared"
    / "threading.py"
)
spec = importlib.util.spec_from_file_location("threading", MODULE_PATH)
threading_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(threading_module)  # type: ignore[union-attr]
XTThreadingManager = threading_module.XTThreadingManager


def test_all_threads_joined():
    manager = XTThreadingManager()
    results = []

    for i in range(3):
        def worker(i=i):
            time.sleep(0.01)
            results.append(i)

        manager.add_thread(worker)

    manager.start_and_wait()

    assert manager.thread_active_list == []
    assert sorted(results) == [0, 1, 2]
    for thread in manager.thread_finished_list:
        assert not thread.is_alive()


def test_thread_exception_propagated():
    manager = XTThreadingManager()

    def worker():
        raise ValueError("boom")

    manager.add_thread(worker)

    with pytest.raises(ValueError):
        manager.start_and_wait()
