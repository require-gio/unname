"""Thread QoS on macOS."""

from __future__ import annotations

import sys
import threading

import pytest

from presidio_sidecar import qos


def test_qos_has_no_effect_outside_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    qos._set_qos_function.cache_clear()
    monkeypatch.setattr(sys, "platform", "win32")
    try:
        assert qos.set_thread_qos(qos.QosClass.UTILITY) is False
    finally:
        qos._set_qos_function.cache_clear()


@pytest.mark.skipif(sys.platform != "darwin", reason="nur macOS")
def test_qos_is_set_in_own_thread() -> None:
    ctypes = pytest.importorskip("ctypes")
    get_qos = ctypes.CDLL(None).qos_class_self
    get_qos.restype = ctypes.c_uint
    measured: list[int] = []

    def _work() -> None:
        assert qos.set_thread_qos(qos.QosClass.UTILITY)
        measured.append(get_qos())

    thread = threading.Thread(target=_work)
    thread.start()
    thread.join()
    assert measured == [qos.QosClass.UTILITY]
