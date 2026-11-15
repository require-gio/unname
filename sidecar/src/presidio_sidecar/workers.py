"""CPU-document pool sizing based on the runtime matrix.

The actual GPU detection comes later. For the initial phase we only need to ensure
that deterministic detection, NER and suspicion checks do not consume RAM for
unlimited documents simultaneously.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import asdict, dataclass
from functools import cache
from typing import Any


@dataclass(frozen=True)
class WorkerCapacity:
    """Result of the local core/RAM estimate."""

    cpu_workers: int
    ocr_workers: int
    logical_cores: int
    memory_bytes: int
    #: performance cores (Apple Silicon: ``hw.perflevel0.logicalcpu``), otherwise
    #: all logical cores. The name-recognition assumes this many threads; the
    #: worker matrix does not yet follow that.
    #: ``0`` means unknown and is treated as all cores.
    performance_cores: int = 0

    def __post_init__(self) -> None:
        if self.performance_cores <= 0:
            object.__setattr__(self, "performance_cores", self.logical_cores)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["memory_gib"] = round(self.memory_bytes / (1024**3), 1)
        return result


def detect_logical_cores() -> int:
    """Report at least one core, even in restricted environments."""

    return max(1, os.cpu_count() or 1)


@cache
def detect_performance_cores() -> int:
    """Performance cores; if unknown, use all logical cores.

    On an M1 Max (8 performance, 2 efficiency cores) GLiNER runs 30 % faster
    with 8 threads than with all 10: a thread on an efficiency core slows the
    others at each synchronization point.
    """

    if sys.platform == "darwin":
        count = _sysctl_int("hw.perflevel0.logicalcpu")
        if count is not None and count > 0:
            return count
    return detect_logical_cores()


def _sysctl_int(name: str) -> int | None:
    try:
        sysctlbyname = ctypes.CDLL(None).sysctlbyname
    except (AttributeError, OSError):
        return None
    value = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    try:
        result = sysctlbyname(
            name.encode("ascii"), ctypes.byref(value), ctypes.byref(size), None, 0
        )
    except OSError:
        return None
    if result != 0 or size.value != ctypes.sizeof(value):
        return None
    return value.value


def detect_physical_memory() -> int:
    """Read installed RAM without extra runtime dependencies."""

    if sys.platform == "win32":
        return _windows_physical_memory()

    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if isinstance(pages, int) and isinstance(page_size, int):
            return max(0, pages * page_size)
    except (AttributeError, OSError, ValueError):
        pass

    # An unknown RAM amount must not lead to too many parallel models.
    # 8 GiB is the conservative MVP lower bound.
    return 8 * 1024**3


def capacity_for(
    logical_cores: int | None = None,
    memory_bytes: int | None = None,
    performance_cores: int | None = None,
) -> WorkerCapacity:
    """Derive pool size from spec §4/5 (max four documents)."""

    cores = max(1, logical_cores if logical_cores is not None else detect_logical_cores())
    memory = max(0, memory_bytes if memory_bytes is not None else detect_physical_memory())
    gib = memory / (1024**3)

    if cores <= 2 or gib < 12:
        cpu_workers = 1
    elif cores <= 4 or gib < 16:
        cpu_workers = 2
    elif cores < 8:
        cpu_workers = 3
    else:
        cpu_workers = 4

    if gib < 12:
        ocr_workers = 0
    elif cores < 8 or gib < 24:
        ocr_workers = 1
    else:
        ocr_workers = 2

    if performance_cores is None:
        performance_cores = detect_performance_cores() if logical_cores is None else cores
    return WorkerCapacity(
        cpu_workers=cpu_workers,
        ocr_workers=ocr_workers,
        logical_cores=cores,
        memory_bytes=memory,
        performance_cores=max(1, min(cores, performance_cores)),
    )


#: Apple Vision gets two OCR lanes when memory is at least this many GiB.
VISION_TWO_LANES_MIN_GIB = 8


def ocr_slots_for(engine: str | None, capacity: WorkerCapacity) -> int:
    """Number of parallel OCR lanes per engine (``0`` means serial).

    Apple Vision consumes 175–270 MiB per call (measured 2026-11-01 on M1 Max,
    A4 @ 300 dpi). Two parallel calls give ~1.5× throughput; more do not help,
    so use two lanes when memory ≥ 8 GiB. Tesseract follows the matrix from spec
    §4/5.
    """

    if engine == "apple-vision":
        return 2 if capacity.memory_bytes / (1024**3) >= VISION_TWO_LANES_MIN_GIB else 1
    return capacity.ocr_workers


def _windows_physical_memory() -> int:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatusEx()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return int(status.total_physical)
    except (AttributeError, OSError):
        pass
    return 8 * 1024**3
