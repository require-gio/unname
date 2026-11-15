"""Unique hardware detection and robust GPU selection.

A GPU reported by PyTorch is only a candidate. It is considered active only
if a small neural test inference has been computed on exactly that device and
the result remained there. Thus, ``torch.cuda.is_available()`` is explicitly
not the success condition.

The selection policy is decoupled from the PyTorch binding. Tests can use a
small backend and need neither CUDA nor Apple hardware.
"""

from __future__ import annotations

import platform
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from .diagnostics import DiagnosticsRecorder

GIB = 1024**3
MIN_AUTO_CUDA_VRAM = 8 * GIB
MIN_AUTO_MPS_MEMORY = 32 * GIB

ProbeStatus = Literal["not_run", "ok", "failed"]


@dataclass(frozen=True, slots=True)
class GpuCapability:
    """A detected GPU; detection alone is not a runtime proof."""

    device: str
    name: str | None
    vram_bytes: int | None
    probe: ProbeStatus = "not_run"
    opt_in_required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "name": self.name,
            "vram_bytes": self.vram_bytes,
            "probe": self.probe,
            "opt_in_required": self.opt_in_required,
        }


@dataclass(frozen=True, slots=True)
class HardwareCapabilities:
    """Device base captured at startup, unchanged thereafter."""

    logical_cores: int
    memory_bytes: int
    system: str
    machine: str
    gpus: tuple[GpuCapability, ...] = ()
    detection_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeviceSelection:
    """Effective device selection for health, NER, and UI."""

    requested: str
    active: str
    probe: ProbeStatus
    gpus: tuple[GpuCapability, ...]
    mps_fallback_env: bool
    selection_reason: str
    fallback_from: str | None = None
    fallback_reason: str | None = None

    @property
    def available(self) -> tuple[str, ...]:
        """Detected hardware, not necessarily successfully tested."""

        return tuple(gpu.device for gpu in self.gpus)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "active": self.active,
            "probe": self.probe,
            "mps_fallback_env": self.mps_fallback_env,
            "available": list(self.available),
            "gpus": [gpu.as_dict() for gpu in self.gpus],
            "selection_reason": self.selection_reason,
            "fallback_from": self.fallback_from,
            "fallback_reason": self.fallback_reason,
        }


class HardwareBackend(Protocol):
    """Narrow boundary to PyTorch, keeping selection testable without GPU."""

    def cuda_devices(self) -> Sequence[GpuCapability]: ...

    def mps_available(self) -> bool: ...

    def test_inference(self, device: str) -> str:
        """Computes forward and returns the actual result device."""
        ...


class TorchHardwareBackend:
    """Binding to PyTorch, if installed.

        Since , PyTorch is no longer part of the sidecar: Name
    detection runs as ONNX on the CPU. Without PyTorch, there is no GPU
    candidate – this is the normal case, not a detection error.
    """

    def __init__(self) -> None:
        self._module: Any | None = None
        self._missing = False

    def _torch(self) -> Any | None:
        if self._module is None and not self._missing:
            try:
                import torch
            except ImportError:
                self._missing = True
                return None
            self._module = torch
        return self._module

    def cuda_devices(self) -> tuple[GpuCapability, ...]:
        torch = self._torch()
        if torch is None:
            return ()
        devices: list[GpuCapability] = []
        # ``device_count`` serves only candidate detection. Only
        # ``test_inference`` may promote a candidate to an active device.
        for index in range(int(torch.cuda.device_count())):
            properties = torch.cuda.get_device_properties(index)
            memory = _nonnegative_int(getattr(properties, "total_memory", None))
            devices.append(
                GpuCapability(
                    device=f"cuda:{index}",
                    name=_optional_text(getattr(properties, "name", None)),
                    vram_bytes=memory,
                    opt_in_required=memory is None or memory < MIN_AUTO_CUDA_VRAM,
                )
            )
        return tuple(devices)

    def mps_available(self) -> bool:
        torch = self._torch()
        if torch is None:
            return False
        backend = getattr(getattr(torch, "backends", None), "mps", None)
        return bool(
            backend is not None
            and callable(getattr(backend, "is_built", None))
            and backend.is_built()
            and callable(getattr(backend, "is_available", None))
            and backend.is_available()
        )

    def test_inference(self, device: str) -> str:
        """Small real network inference instead of an availability flag."""

        torch = self._torch()
        if torch is None:
            raise RuntimeError("Ohne PyTorch gibt es keine Geräte-Testinferenz.")
        layer = torch.nn.Sequential(
            torch.nn.Linear(4, 8),
            torch.nn.GELU(),
            torch.nn.Linear(8, 2),
        ).to(device)
        sample = torch.tensor(
            [[0.25, -0.5, 0.75, 1.0]],
            dtype=torch.float32,
            device=device,
        )
        with torch.inference_mode():
            output = layer(sample)

        # The back-transmission synchronizes the computation path and simultaneously
        # checks that no invalid numbers pass as apparent success.
        actual_device = str(output.device)
        finite = bool(torch.isfinite(output).all().to("cpu").item())
        if tuple(output.shape) != (1, 2) or not finite:
            raise RuntimeError("Die Geräte-Testinferenz lieferte kein gültiges Ergebnis.")
        return actual_device


def detect_hardware(
    *,
    logical_cores: int,
    memory_bytes: int,
    backend: HardwareBackend | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> HardwareCapabilities:
    """Detects GPU types without reporting a mere finding as ready for use."""

    detector = backend or TorchHardwareBackend()
    gpus: list[GpuCapability] = []
    errors: list[str] = []
    try:
        gpus.extend(detector.cuda_devices())
    except Exception as error:
        errors.append(f"cuda:{type(error).__name__}")
    try:
        if detector.mps_available():
            gpus.append(
                GpuCapability(
                    device="mps",
                    name="Apple Metal Performance Shaders",
                    vram_bytes=None,
                    opt_in_required=memory_bytes < MIN_AUTO_MPS_MEMORY,
                )
            )
    except Exception as error:
        errors.append(f"mps:{type(error).__name__}")

    return HardwareCapabilities(
        logical_cores=max(1, logical_cores),
        memory_bytes=max(0, memory_bytes),
        system=system or platform.system(),
        machine=machine or platform.machine(),
        gpus=tuple(_unique_gpus(gpus)),
        detection_errors=tuple(errors),
    )


def select_device(
    capabilities: HardwareCapabilities,
    *,
    requested: str = "auto",
    mps_fallback_env: bool = False,
    backend: HardwareBackend | None = None,
    diagnostics: DiagnosticsRecorder | None = None,
) -> DeviceSelection:
    """Applies Windows/macOS matrix, override and test inference once."""

    detector = backend or TorchHardwareBackend()
    normalized_request = _normalize_request(requested)
    gpus = capabilities.gpus

    if mps_fallback_env and diagnostics is not None:
        diagnostics.record(
            "device_selection",
            "PYTORCH_ENABLE_MPS_FALLBACK=1 ist als Kompatibilitätsmodus aktiv.",
            severity="warning",
            environment="PYTORCH_ENABLE_MPS_FALLBACK",
            enabled=True,
        )

    if normalized_request == "cpu":
        return DeviceSelection(
            requested=normalized_request,
            active="cpu",
            probe="not_run",
            gpus=gpus,
            mps_fallback_env=mps_fallback_env,
            selection_reason="explicit_cpu",
        )

    candidate, reason = _candidate_for(
        capabilities,
        normalized_request,
    )
    if candidate is None:
        detection_errors = _relevant_detection_errors(
            capabilities.detection_errors,
            normalized_request,
        )
        if detection_errors:
            fallback_from = detection_errors[0].partition(":")[0]
            selection = DeviceSelection(
                requested=normalized_request,
                active="cpu",
                probe="failed",
                gpus=gpus,
                mps_fallback_env=mps_fallback_env,
                selection_reason="fallback_cpu",
                fallback_from=fallback_from,
                fallback_reason="device_detection_failed",
            )
            for item in detection_errors:
                backend_name, _, error_type = item.partition(":")
                _record_fallback(
                    replace(selection, fallback_from=backend_name),
                    diagnostics,
                    error_type=error_type or None,
                )
            return selection

        if normalized_request != "auto":
            fallback_reason = (
                "invalid_device"
                if not _is_supported_request(normalized_request)
                else "device_unavailable"
            )
            selection = DeviceSelection(
                requested=normalized_request,
                active="cpu",
                probe="failed",
                gpus=gpus,
                mps_fallback_env=mps_fallback_env,
                selection_reason="fallback_cpu",
                fallback_from=normalized_request,
                fallback_reason=fallback_reason,
            )
            _record_fallback(selection, diagnostics, error_type=None)
            return selection

        selection = DeviceSelection(
            requested="auto",
            active="cpu",
            probe="not_run",
            gpus=gpus,
            mps_fallback_env=mps_fallback_env,
            selection_reason=reason,
        )
        if gpus and diagnostics is not None:
            diagnostics.record(
                "device_selection",
                "GPU erkannt, nach der Laufzeitmatrix bleibt der CPU-Modus aktiv.",
                detected=list(selection.available),
                reason=reason,
            )
        return selection

    try:
        actual_device = _normalize_actual_device(detector.test_inference(candidate.device))
        if actual_device != candidate.device:
            raise DeviceProbeMismatch(actual_device)
    except Exception as error:
        failed_gpus = _with_probe(gpus, candidate.device, "failed")
        reason_code = (
            "probe_device_mismatch"
            if isinstance(error, DeviceProbeMismatch)
            else "test_inference_failed"
        )
        selection = DeviceSelection(
            requested=normalized_request,
            active="cpu",
            probe="failed",
            gpus=failed_gpus,
            mps_fallback_env=mps_fallback_env,
            selection_reason="fallback_cpu",
            fallback_from=candidate.device,
            fallback_reason=reason_code,
        )
        _record_fallback(selection, diagnostics, error_type=type(error).__name__)
        return selection

    selection = DeviceSelection(
        requested=normalized_request,
        active=candidate.device,
        probe="ok",
        gpus=_with_probe(gpus, candidate.device, "ok"),
        mps_fallback_env=mps_fallback_env,
        selection_reason=reason,
    )
    if diagnostics is not None:
        diagnostics.record(
            "device_selection",
            "GPU-Hardware durch eine lokale neuronale Testinferenz bestätigt.",
            device=candidate.device,
            reason=reason,
        )
    return selection


def runtime_fallback(
    selection: DeviceSelection,
    *,
    reason: str,
    error_type: str,
    diagnostics: DiagnosticsRecorder | None = None,
) -> DeviceSelection:
    """Corrects health if a later model inference fails."""

    if selection.active == "cpu":
        return selection
    failed = replace(
        selection,
        active="cpu",
        probe="failed",
        gpus=_with_probe(selection.gpus, selection.active, "failed"),
        selection_reason="runtime_fallback",
        fallback_from=selection.active,
        fallback_reason=reason,
    )
    _record_fallback(failed, diagnostics, error_type=error_type)
    return failed


class DeviceProbeMismatch(RuntimeError):
    """The probe ran, but its result was not on the requested device."""

    def __init__(self, actual_device: str) -> None:
        super().__init__("Die Testinferenz lief nicht auf dem angeforderten Gerät.")
        self.actual_device = actual_device


def _candidate_for(
    capabilities: HardwareCapabilities,
    requested: str,
) -> tuple[GpuCapability | None, str]:
    by_device = {gpu.device: gpu for gpu in capabilities.gpus}
    if requested != "auto":
        normalized = "cuda:0" if requested == "cuda" else requested
        return by_device.get(normalized), "explicit_gpu"

    for gpu in capabilities.gpus:
        if gpu.device.startswith("cuda:") and not gpu.opt_in_required:
            return gpu, "auto_cuda"

    apple_silicon = capabilities.machine.casefold() in {"arm64", "aarch64"}
    mps = by_device.get("mps")
    if mps is not None and apple_silicon and capabilities.memory_bytes >= MIN_AUTO_MPS_MEMORY:
        return mps, "auto_mps"

    if any(gpu.opt_in_required for gpu in capabilities.gpus):
        return None, "gpu_opt_in_required"
    return None, "auto_cpu"


def _record_fallback(
    selection: DeviceSelection,
    diagnostics: DiagnosticsRecorder | None,
    *,
    error_type: str | None,
) -> None:
    if diagnostics is None or selection.fallback_from is None:
        return
    category = (
        "mps_fallback"
        if selection.fallback_from == "mps"
        else "gpu_fallback"
        if selection.fallback_from.startswith("cuda")
        else "device_selection"
    )
    diagnostics.record(
        category,
        "Der angeforderte GPU-Pfad ist nicht sicher nutzbar; CPU-Fallback ist aktiv.",
        severity="warning",
        from_device=selection.fallback_from,
        to_device="cpu",
        reason=selection.fallback_reason,
        error_type=error_type,
    )


def _normalize_request(value: str) -> str:
    normalized = str(value).strip().casefold()
    prefix, separator, index = normalized.partition(":")
    if prefix == "cuda" and separator == ":" and index.isdigit():
        return f"cuda:{int(index)}"
    return normalized or "auto"


def _is_supported_request(value: str) -> bool:
    if value in {"auto", "cpu", "mps", "cuda"}:
        return True
    prefix, separator, index = value.partition(":")
    return prefix == "cuda" and separator == ":" and index.isdigit()


def _normalize_actual_device(value: object) -> str:
    normalized = str(value).strip().casefold()
    if normalized == "cuda":
        return "cuda:0"
    if normalized == "mps:0":
        return "mps"
    return normalized


def _relevant_detection_errors(
    errors: tuple[str, ...],
    requested: str,
) -> tuple[str, ...]:
    if requested == "auto":
        return errors
    if requested.startswith("cuda"):
        return tuple(error for error in errors if error.startswith("cuda:"))
    if requested == "mps":
        return tuple(error for error in errors if error.startswith("mps:"))
    return ()


def _with_probe(
    gpus: tuple[GpuCapability, ...],
    device: str,
    probe: ProbeStatus,
) -> tuple[GpuCapability, ...]:
    return tuple(replace(gpu, probe=probe) if gpu.device == device else gpu for gpu in gpus)


def _unique_gpus(gpus: Sequence[GpuCapability]) -> list[GpuCapability]:
    unique: dict[str, GpuCapability] = {}
    for gpu in gpus:
        unique.setdefault(gpu.device, gpu)
    return list(unique.values())


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
