"""Parallelism limit for local OCR without real Tesseract processes."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock

import pytest

from presidio_sidecar.extraction import LimitedOcrBackend, OcrWord

WAIT_SECONDS = 2.0
WORD = OcrWord(
    text="Max",
    left=1,
    top=2,
    width=3,
    height=4,
    line_id=(1, 1, 1, 1),
    confidence=0.99,
)


class BlockingOcr:
    """Keeps calls open and measures the actual backend parallelism."""

    def __init__(self, expected_parallel: int) -> None:
        self.expected_parallel = expected_parallel
        self.release = Event()
        self.expected_entered = Event()
        self._lock = Lock()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.received: list[tuple[object, tuple[str, ...]]] = []

    def recognize(
        self,
        image: object,
        *,
        languages: tuple[str, ...],
    ) -> tuple[OcrWord, ...]:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.received.append((image, languages))
            if self.active == self.expected_parallel:
                self.expected_entered.set()
        try:
            if not self.release.wait(WAIT_SECONDS):
                raise AssertionError("Test hat das blockierte OCR-Backend nicht freigegeben.")
            return (WORD,)
        finally:
            with self._lock:
                self.active -= 1


class FlakyOcr:
    def __init__(self) -> None:
        self.calls = 0

    def recognize(
        self,
        image: object,
        *,
        languages: tuple[str, ...],
    ) -> tuple[OcrWord, ...]:
        del image, languages
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("kontrollierter OCR-Testfehler")
        return (WORD,)


@pytest.mark.parametrize(
    ("parallel_slots", "expected_parallel"),
    [
        (0, 1),
        (1, 1),
        (2, 2),
    ],
)
def test_limiter_limits_backend_without_timing_assumptions(
    parallel_slots: int,
    expected_parallel: int,
) -> None:
    total_calls = expected_parallel + 1
    backend = BlockingOcr(expected_parallel)
    limited = LimitedOcrBackend(backend, parallel_slots=parallel_slots)
    all_attempted = Event()
    attempt_lock = Lock()
    attempts = 0

    def invoke(index: int) -> tuple[OcrWord, ...]:
        nonlocal attempts
        with attempt_lock:
            attempts += 1
            if attempts == total_calls:
                all_attempted.set()
        return limited.recognize(index, languages=("deu", "eng"))

    executor = ThreadPoolExecutor(max_workers=total_calls)
    futures: list[Future[tuple[OcrWord, ...]]] = []
    try:
        futures = [executor.submit(invoke, index) for index in range(total_calls)]
        assert all_attempted.wait(WAIT_SECONDS), "Nicht alle Testaufrufe wurden gestartet."
        assert backend.expected_entered.wait(WAIT_SECONDS), "OCR-Grenze wurde nicht erreicht."

        with backend._lock:
            assert backend.active == expected_parallel
            assert backend.calls == expected_parallel
            assert backend.max_active == expected_parallel
    finally:
        backend.release.set()
        executor.shutdown(wait=True, cancel_futures=True)

    assert [future.result(timeout=WAIT_SECONDS) for future in futures] == [(WORD,)] * total_calls
    assert backend.calls == total_calls
    assert backend.max_active == expected_parallel
    assert backend.active == 0
    assert {languages for _, languages in backend.received} == {("deu", "eng")}


def test_backend_error_releases_serial_slot() -> None:
    backend = FlakyOcr()
    limited = LimitedOcrBackend(backend, parallel_slots=0)

    with pytest.raises(RuntimeError, match="kontrollierter OCR-Testfehler"):
        limited.recognize(object(), languages=("deu",))

    assert limited.recognize(object(), languages=("eng",)) == (WORD,)
    assert backend.calls == 2


@pytest.mark.parametrize("parallel_slots", [-2, -1])
def test_negative_slot_count_is_rejected(parallel_slots: int) -> None:
    with pytest.raises(ValueError, match="nicht negativ"):
        LimitedOcrBackend(FlakyOcr(), parallel_slots=parallel_slots)


def test_slot_count_must_be_integer() -> None:
    with pytest.raises(TypeError, match="ganze Zahl"):
        LimitedOcrBackend(FlakyOcr(), parallel_slots=1.5)  # type: ignore[arg-type]
