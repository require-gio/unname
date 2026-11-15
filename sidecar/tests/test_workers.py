"""CPU pool sizing based on the documented runtime matrix."""

from __future__ import annotations

import sys

import pytest

from presidio_sidecar import workers
from presidio_sidecar.workers import capacity_for

GIB = 1024**3


@pytest.mark.parametrize(
    ("cores", "memory_gib", "cpu_workers", "ocr_workers"),
    [
        (2, 8, 1, 0),
        (4, 16, 2, 1),
        (6, 16, 3, 1),
        (8, 16, 4, 1),
        (8, 24, 4, 2),
        (32, 64, 4, 2),
    ],
)
def test_runtime_matrix_scales_both_pools(
    cores: int,
    memory_gib: int,
    cpu_workers: int,
    ocr_workers: int,
) -> None:
    capacity = capacity_for(logical_cores=cores, memory_bytes=memory_gib * GIB)

    assert capacity.cpu_workers == cpu_workers
    assert capacity.ocr_workers == ocr_workers


def test_many_cores_do_not_overrule_limited_ram() -> None:
    assert capacity_for(logical_cores=32, memory_bytes=8 * GIB).cpu_workers == 1


def test_unknown_or_implausible_values_remain_conservative() -> None:
    capacity = capacity_for(logical_cores=0, memory_bytes=-1)

    assert capacity.logical_cores == 1
    assert capacity.memory_bytes == 0
    assert capacity.cpu_workers == 1
    assert capacity.ocr_workers == 0


def test_performance_cores_listed_alongside_all_cores_in_capacity() -> None:
    capacity = capacity_for(logical_cores=10, memory_bytes=64 * GIB, performance_cores=8)

    assert capacity.as_dict()["performance_cores"] == 8
    assert capacity.as_dict()["logical_cores"] == 10
    # E-T2: Worker matrix still based on all cores.
    assert capacity.cpu_workers == 4


def test_performance_cores_never_exceed_all_cores() -> None:
    assert (
        capacity_for(logical_cores=4, memory_bytes=16 * GIB, performance_cores=8).performance_cores
        == 4
    )
    assert (
        capacity_for(logical_cores=4, memory_bytes=16 * GIB, performance_cores=0).performance_cores
        == 1
    )


def test_without_system_info_all_cores_count(monkeypatch: pytest.MonkeyPatch) -> None:
    workers.detect_performance_cores.cache_clear()
    monkeypatch.setattr(workers, "_sysctl_int", lambda name: None)
    monkeypatch.setattr(workers, "detect_logical_cores", lambda: 6)
    try:
        assert workers.detect_performance_cores() == 6
    finally:
        workers.detect_performance_cores.cache_clear()


@pytest.mark.skipif(sys.platform != "darwin", reason="nur macOS")
def test_macos_reports_performance_cores_via_sysctl() -> None:
    count = workers._sysctl_int("hw.perflevel0.logicalcpu")

    assert count is not None
    assert 1 <= count <= workers.detect_logical_cores()
    assert workers._sysctl_int("hw.gibt.es.nicht") is None
