"""Thread QoS handling (macOS, spec §T3).

macOS schedules threads by QoS class on performance and efficiency cores.
Documents awaited by the user run with ``USER_INITIATED``; cleanup and
pre-warming use ``UTILITY``. On other platforms this has no effect.
"""

from __future__ import annotations

import ctypes
import enum
import sys
from functools import cache


class QosClass(enum.IntEnum):
    """Values from ``<sys/qos.h>``."""

    USER_INTERACTIVE = 0x21
    USER_INITIATED = 0x19
    DEFAULT = 0x15
    UTILITY = 0x11
    BACKGROUND = 0x09


@cache
def _set_qos_function() -> object | None:
    if sys.platform != "darwin":
        return None
    try:
        function = ctypes.CDLL(None).pthread_set_qos_class_self_np
    except (AttributeError, OSError):
        return None
    function.argtypes = (ctypes.c_uint, ctypes.c_int)
    function.restype = ctypes.c_int
    return function


def set_thread_qos(qos: QosClass) -> bool:
    """Set the QoS of the calling thread. Returns ``True`` on success."""

    function = _set_qos_function()
    if function is None:
        return False
    try:
        return function(int(qos), 0) == 0  # type: ignore[operator]
    except OSError:
        return False


def user_initiated() -> None:
    """Initializer for thread pools whose work is expected by a user."""

    set_thread_qos(QosClass.USER_INITIATED)
