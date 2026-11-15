"""Hardware matrix, real probe limit and visible CPU fallbacks."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.hardware import (
    GIB,
    MIN_AUTO_CUDA_VRAM,
    DeviceSelection,
    GpuCapability,
    HardwareBackend,
    HardwareCapabilities,
    TorchHardwareBackend,
    detect_hardware,
    runtime_fallback,
    select_device,
)


@dataclass
class FakeBackend(HardwareBackend):
    cuda: tuple[GpuCapability, ...] = ()
    mps: bool = False
    actual_devices: dict[str, str] = field(default_factory=dict)
    failures: dict[str, Exception] = field(default_factory=dict)
    probe_calls: list[str] = field(default_factory=list)
    cuda_error: Exception | None = None
    mps_error: Exception | None = None

    def cuda_devices(self) -> tuple[GpuCapability, ...]:
        if self.cuda_error is not None:
            raise self.cuda_error
        return self.cuda

    def mps_available(self) -> bool:
        if self.mps_error is not None:
            raise self.mps_error
        return self.mps

    def test_inference(self, device: str) -> str:
        self.probe_calls.append(device)
        if device in self.failures:
            raise self.failures[device]
        return self.actual_devices.get(device, device)


def capabilities(
    backend: FakeBackend,
    *,
    memory_gib: int = 16,
    machine: str = "x86_64",
) -> HardwareCapabilities:
    return detect_hardware(
        logical_cores=4,
        memory_bytes=memory_gib * GIB,
        backend=backend,
        system="TestOS",
        machine=machine,
    )


def nvidia(device: str = "cuda:0", *, vram: int = 8 * GIB) -> GpuCapability:
    return GpuCapability(
        device=device,
        name="Test NVIDIA",
        vram_bytes=vram,
        opt_in_required=vram < MIN_AUTO_CUDA_VRAM,
    )


def test_auto_without_gpu_stays_on_cpu_without_needless_probe() -> None:
    backend = FakeBackend()

    selection = select_device(capabilities(backend), backend=backend)

    assert selection.active == "cpu"
    assert selection.probe == "not_run"
    assert selection.selection_reason == "auto_cpu"
    assert backend.probe_calls == []


@pytest.mark.parametrize(
    ("vram", "active", "reason"),
    [
        (8 * GIB, "cuda:0", "auto_cuda"),
        (8 * GIB - 1, "cpu", "gpu_opt_in_required"),
    ],
)
def test_cuda_auto_starts_exactly_at_eight_gib(
    vram: int,
    active: str,
    reason: str,
) -> None:
    backend = FakeBackend(cuda=(nvidia(vram=vram),))

    selection = select_device(capabilities(backend), backend=backend)

    assert selection.active == active
    assert selection.selection_reason == reason
    assert backend.probe_calls == (["cuda:0"] if active == "cuda:0" else [])


def test_cuda_under_eight_gib_is_possible_via_environment_opt_in() -> None:
    backend = FakeBackend(cuda=(nvidia(vram=4 * GIB),))

    selection = select_device(
        capabilities(backend),
        requested="cuda:0",
        backend=backend,
    )

    assert selection.active == "cuda:0"
    assert selection.probe == "ok"
    assert selection.gpus[0].opt_in_required is True
    assert backend.probe_calls == ["cuda:0"]


def test_cpu_override_wins_over_auto_selection() -> None:
    backend = FakeBackend(cuda=(nvidia(),))

    selection = select_device(
        capabilities(backend),
        requested=" CPU ",
        backend=backend,
    )

    assert selection.requested == "cpu"
    assert selection.active == "cpu"
    assert selection.selection_reason == "explicit_cpu"
    assert backend.probe_calls == []


def test_multiple_cuda_devices_and_explicit_index() -> None:
    backend = FakeBackend(
        cuda=(
            nvidia("cuda:0", vram=4 * GIB),
            nvidia("cuda:1", vram=12 * GIB),
        )
    )
    detected = capabilities(backend)

    automatic = select_device(detected, backend=backend)
    explicit = select_device(detected, requested="cuda:0", backend=backend)

    assert automatic.active == "cuda:1"
    assert explicit.active == "cuda:0"
    assert backend.probe_calls == ["cuda:1", "cuda:0"]


def test_silent_device_switch_in_probe_becomes_cpu_fallback() -> None:
    secret = "Treiberfehler mit vertraulichem Text"
    backend = FakeBackend(
        cuda=(nvidia(),),
        actual_devices={"cuda:0": "cpu"},
        failures={"nicht-verwendet": RuntimeError(secret)},
    )
    recorder = DiagnosticsRecorder()

    selection = select_device(
        capabilities(backend),
        backend=backend,
        diagnostics=recorder,
    )

    assert selection.active == "cpu"
    assert selection.probe == "failed"
    assert selection.fallback_from == "cuda:0"
    assert selection.fallback_reason == "probe_device_mismatch"
    assert selection.gpus[0].probe == "failed"
    entries = recorder.as_dicts()
    assert [entry["category"] for entry in entries] == ["gpu_fallback"]
    assert secret not in str(entries)


def test_probe_exception_remains_content_free_and_sidecar_usable() -> None:
    secret = "Kundengeheimnis-4711"
    backend = FakeBackend(
        cuda=(nvidia(),),
        failures={"cuda:0": RuntimeError(secret)},
    )
    recorder = DiagnosticsRecorder()

    selection = select_device(
        capabilities(backend),
        backend=backend,
        diagnostics=recorder,
    )

    assert selection.active == "cpu"
    assert selection.fallback_reason == "test_inference_failed"
    assert recorder.as_dicts()[0]["details"]["error_type"] == "RuntimeError"
    assert secret not in str(selection.as_dict())
    assert secret not in str(recorder.as_dicts())


def test_mps_matrix_probe_and_automatic_fallback() -> None:
    working = FakeBackend(mps=True, actual_devices={"mps": "mps:0"})
    automatic = select_device(
        capabilities(working, memory_gib=32, machine="arm64"),
        backend=working,
    )
    assert automatic.active == "mps"
    assert automatic.probe == "ok"

    broken = FakeBackend(mps=True, failures={"mps": RuntimeError("Metal kaputt")})
    recorder = DiagnosticsRecorder()
    fallback = select_device(
        capabilities(broken, memory_gib=16, machine="arm64"),
        requested="mps",
        backend=broken,
        diagnostics=recorder,
    )
    assert fallback.active == "cpu"
    assert fallback.fallback_from == "mps"
    assert recorder.as_dicts()[0]["category"] == "mps_fallback"


def test_mps_under_32_gib_remains_automatically_optional() -> None:
    backend = FakeBackend(mps=True)

    selection = select_device(
        capabilities(backend, memory_gib=16, machine="arm64"),
        backend=backend,
    )

    assert selection.active == "cpu"
    assert selection.available == ("mps",)
    assert selection.gpus[0].opt_in_required is True
    assert backend.probe_calls == []


def test_mps_fallback_environment_appears_once_in_diagnostic_report() -> None:
    backend = FakeBackend()
    recorder = DiagnosticsRecorder()

    selection = select_device(
        capabilities(backend),
        requested="cpu",
        mps_fallback_env=True,
        backend=backend,
        diagnostics=recorder,
    )

    assert selection.mps_fallback_env is True
    entries = recorder.as_dicts()
    assert len(entries) == 1
    assert entries[0]["category"] == "device_selection"
    assert "PYTORCH_ENABLE_MPS_FALLBACK=1" in entries[0]["message"]


@pytest.mark.parametrize("requested", ["cuda:4", "mps", "quantenkarte"])
def test_unavailable_or_unknown_request_falls_back_visibly(
    requested: str,
) -> None:
    recorder = DiagnosticsRecorder()

    selection = select_device(
        capabilities(FakeBackend()),
        requested=requested,
        backend=FakeBackend(),
        diagnostics=recorder,
    )

    assert selection.active == "cpu"
    assert selection.probe == "failed"
    assert selection.fallback_from == requested
    assert len(recorder.as_dicts()) == 1


def test_later_model_failure_corrects_device_and_logs_once() -> None:
    recorder = DiagnosticsRecorder()
    selected = DeviceSelection(
        requested="auto",
        active="cuda:0",
        probe="ok",
        gpus=(nvidia(),),
        mps_fallback_env=False,
        selection_reason="auto_cuda",
    )

    failed = runtime_fallback(
        selected,
        reason="model_inference_failed",
        error_type="RuntimeError",
        diagnostics=recorder,
    )
    unchanged = runtime_fallback(
        failed,
        reason="model_inference_failed",
        error_type="RuntimeError",
        diagnostics=recorder,
    )

    assert failed.active == "cpu"
    assert failed.probe == "failed"
    assert failed.gpus[0].probe == "failed"
    assert unchanged is failed
    assert [entry["category"] for entry in recorder.as_dicts()] == ["gpu_fallback"]


def test_detection_errors_are_data_instead_of_start_abort() -> None:
    backend = FakeBackend(
        cuda_error=RuntimeError("CUDA-Treiber defekt"),
        mps_error=RuntimeError("MPS nicht initialisierbar"),
    )

    detected = capabilities(backend)
    recorder = DiagnosticsRecorder()
    selection = select_device(detected, backend=backend, diagnostics=recorder)

    assert detected.gpus == ()
    assert detected.detection_errors == ("cuda:RuntimeError", "mps:RuntimeError")
    assert selection.active == "cpu"
    assert selection.probe == "failed"
    assert selection.fallback_reason == "device_detection_failed"
    assert [entry["category"] for entry in recorder.as_dicts()] == [
        "gpu_fallback",
        "mps_fallback",
    ]


def test_without_pytorch_no_gpu_candidate_and_no_error() -> None:
    """PyTorch is no longer part of the sidecar."""

    backend = TorchHardwareBackend()
    backend._missing = True  # noqa: SLF001 – as in the package environment
    assert backend.cuda_devices() == ()
    assert backend.mps_available() is False
    with pytest.raises(RuntimeError):
        backend.test_inference("cpu")
    finding = detect_hardware(logical_cores=4, memory_bytes=8 * 1024**3, backend=backend)
    assert (finding.gpus, finding.detection_errors) == ((), ())
