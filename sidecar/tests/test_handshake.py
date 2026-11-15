"""stdout log: one JSON line per event, flushed immediately."""

from __future__ import annotations

import io
import json

from presidio_sidecar import PROTOCOL_VERSION
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.handshake import emit, emit_ready, emit_stopping


def test_ready_emits_port_and_token() -> None:
    stream = io.StringIO()
    emit_ready(port=54321, token="geheim", pid=42, version="0.1.0", stream=stream)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1, "Genau eine Zeile je Ereignis"

    payload = json.loads(lines[0])
    assert payload == {
        "event": "ready",
        "protocol": PROTOCOL_VERSION,
        "port": 54321,
        "token": "geheim",
        "pid": 42,
        "version": "0.1.0",
    }


def test_ready_with_unix_socket_omits_port() -> None:
    stream = io.StringIO()
    emit_ready(socket="/tmp/x/s", token="geheim", pid=42, version="0.1.0", stream=stream)
    payload = json.loads(stream.getvalue())
    assert payload["socket"] == "/tmp/x/s"
    assert "port" not in payload


def test_ready_requires_exactly_one_endpoint() -> None:
    import pytest

    for endpoint in ({}, {"port": 1, "socket": "/tmp/s"}):
        with pytest.raises(ValueError):
            emit_ready(token="t", pid=1, version="0", stream=io.StringIO(), **endpoint)


def test_events_remain_single_line() -> None:
    """A newline would break the shell's data stream."""
    stream = io.StringIO()
    emit("diagnostic", stream=stream, message="Zeile eins\nZeile zwei")
    assert len(stream.getvalue().splitlines()) == 1


def test_stopping() -> None:
    stream = io.StringIO()
    emit_stopping(reason="shutdown", stream=stream)
    assert json.loads(stream.getvalue())["event"] == "stopping"


def test_diagnostic_entry_also_goes_to_stdout(capsys) -> None:  # noqa: ANN001 — pytest-Fixture
    """The shell writes the overall report and must see the entries."""
    DiagnosticsRecorder().record("gpu_fallback", "CUDA nicht nutzbar.", severity="warning")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["event"] == "diagnostic"
    assert payload["category"] == "gpu_fallback"
    assert payload["severity"] == "warning"


def test_ring_buffer_limits_report() -> None:
    recorder = DiagnosticsRecorder(capacity=3)
    for index in range(5):
        recorder.record("startup", f"Eintrag {index}")
    assert [entry.message for entry in recorder.entries()] == [
        "Eintrag 2",
        "Eintrag 3",
        "Eintrag 4",
    ]
