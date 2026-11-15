"""Sidecar runs as a real process, started by the Tauri shell.

These tests cover the milestone-0 aspects that in-process tests cannot show:
random port selection, stdout handshake, reachability, and clean shutdown
when the shell exits.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from queue import Empty, Queue

import pytest

READY_TIMEOUT_S = 30
EXIT_TIMEOUT_S = 15


class Sidecar:
    def __init__(
        self,
        process: subprocess.Popen[str],
        ready: dict,
        environment: dict[str, str],
    ) -> None:
        self.process = process
        self.environment = environment
        self.port: int = ready["port"]
        self.token: str = ready["token"]
        self.pid: int = ready["pid"]

    def get(self, path: str, *, token: str | None = ...) -> tuple[int, dict]:  # type: ignore[assignment]
        used = self.token if token is ... else token
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if used is not None:
            request.add_header("Authorization", f"Bearer {used}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")


def _read_ready(
    process: subprocess.Popen[str],
    *,
    timeout_s: float = READY_TIMEOUT_S,
) -> dict:
    assert process.stdout is not None
    result: Queue[dict | Exception] = Queue(maxsize=1)

    # ``readline`` may block indefinitely on Windows and POSIX. Direct timeout
    # does not work. A daemon reader can block while the test thread uses a real
    # timeout; the pipe is closed when the process ends.
    def read_stdout() -> None:
        try:
            for line in process.stdout:
                payload = json.loads(line)
                if payload.get("event") == "ready":
                    result.put(payload)
                    return
            result.put(AssertionError(f"Sidecar endete vor dem Handshake (Code {process.poll()})."))
        except Exception as error:  # pragma: no cover - forwarded to test thread
            result.put(error)

    threading.Thread(target=read_stdout, name="sidecar-ready-reader", daemon=True).start()
    try:
        ready = result.get(timeout=timeout_s)
    except Empty as error:
        raise AssertionError("Keine ready-Zeile innerhalb der Wartezeit.") from error
    if isinstance(ready, Exception):
        raise ready
    return ready


@pytest.fixture
def sidecar(tmp_path: Path) -> Iterator[Sidecar]:
    environment = {
        **os.environ,
        "PRESIDIO_STATE_DIR": str(tmp_path / "private-state"),
        # The process test checks lifecycle, not CI runner hardware. GPU selection
        # is tested deterministically against the injectable backend.
        "PRESIDIO_DEVICE": "cpu",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "presidio_sidecar"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=environment,
    )
    try:
        yield Sidecar(process, _read_ready(process), environment)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=EXIT_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                process.kill()


def test_handshake_reports_random_port_and_token(sidecar: Sidecar) -> None:
    assert sidecar.port > 0
    assert sidecar.port != 8000, "Kein fester Port"
    assert len(sidecar.token) >= 32


def test_health_is_reachable_with_token(sidecar: Sidecar) -> None:
    status, body = sidecar.get("/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["components"]["presidio-analyzer"] is not None


def test_health_is_rejected_without_token(sidecar: Sidecar) -> None:
    status, _ = sidecar.get("/health", token=None)
    assert status == 401


def test_startup_line_goes_to_stdout_not_log(sidecar: Sidecar) -> None:
    """After the handshake a startup diagnostic entry is emitted as a JSON line."""
    assert sidecar.process.stdout is not None
    payload = json.loads(sidecar.process.stdout.readline())
    assert payload["event"] == "diagnostic"
    assert payload["category"] == "startup"
    assert payload["details"]["host"] == "127.0.0.1"


def test_sidecar_exits_when_the_shell_goes_away(sidecar: Sidecar) -> None:
    """No orphaned process should remain with an open port and loaded models."""
    assert sidecar.process.stdin is not None
    sidecar.process.stdin.close()
    assert sidecar.process.wait(timeout=EXIT_TIMEOUT_S) == 0


def test_two_sidecars_get_different_ports_and_token(sidecar: Sidecar) -> None:
    second = subprocess.Popen(
        [sys.executable, "-m", "presidio_sidecar"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=sidecar.environment,
    )
    try:
        ready = _read_ready(second)
        assert ready["port"] != sidecar.port
        assert ready["token"] != sidecar.token
    finally:
        second.terminate()
        second.wait(timeout=EXIT_TIMEOUT_S)


def test_binding_to_non_loopback_is_denied() -> None:
    """A PII service bound to 0.0.0.0 would be reachable from the entire network."""
    result = subprocess.run(
        [sys.executable, "-m", "presidio_sidecar", "--host", "0.0.0.0"],
        capture_output=True,
        text=True,
        timeout=EXIT_TIMEOUT_S,
    )
    assert result.returncode == 2
    assert result.stdout == "", "Bei einem Fehlstart darf keine Handshake-Zeile erscheinen"
    assert "Loopback" in result.stderr


def test_ready_timeout_does_not_block_on_silent_stdout_pipe() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    started = time.monotonic()
    try:
        with pytest.raises(AssertionError, match="Keine ready-Zeile"):
            _read_ready(process, timeout_s=0.1)
        assert time.monotonic() - started < 1
    finally:
        process.kill()
        process.wait(timeout=EXIT_TIMEOUT_S)


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str) -> None:
        super().__init__("localhost", timeout=10)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(self._path)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-Domain-Socket nur macOS/Linux")
def test_unix_socket_instead_of_port(tmp_path: Path) -> None:
    """Test that using a Unix domain socket (via --uds) does not open a TCP port;
    the socket file is created with mode 0600 and removed when the process exits."""
    import tempfile

    # short socket path; pytest tmp_path can be too long on macOS.
    with tempfile.TemporaryDirectory(prefix="uds-", dir="/tmp") as directory:
        os.chmod(directory, 0o700)
        path = os.path.join(directory, "s")
        environment = {
            **os.environ,
            "PRESIDIO_STATE_DIR": str(tmp_path / "private-state"),
            "PRESIDIO_DEVICE": "cpu",
        }
        process = subprocess.Popen(
            [sys.executable, "-m", "presidio_sidecar", "--uds", path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=environment,
        )
        try:
            ready = _read_ready(process)
            assert ready["socket"] == path
            assert "port" not in ready
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

            connection = _UnixHTTPConnection(path)
            authorization = {"Authorization": f"Bearer {ready['token']}"}
            connection.request("GET", "/health", headers=authorization)
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["status"] == "ok"
            connection.close()

            connection = _UnixHTTPConnection(path)
            connection.request("GET", "/health")
            assert connection.getresponse().status == 401, "Token gilt auch am Socket"
            connection.close()

            assert process.stdin is not None
            process.stdin.close()
            assert process.wait(timeout=EXIT_TIMEOUT_S) == 0
            assert not os.path.lexists(path), "Socket bleibt nicht liegen"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=EXIT_TIMEOUT_S)

        # Even after SIGTERM: uvicorn re-emits the signal at the end.
        process = subprocess.Popen(
            [sys.executable, "-m", "presidio_sidecar", "--uds", path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=environment,
        )
        try:
            _read_ready(process)
            process.terminate()
            process.wait(timeout=EXIT_TIMEOUT_S)
            assert not os.path.lexists(path), "Socket bleibt nach SIGTERM nicht liegen"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=EXIT_TIMEOUT_S)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-Domain-Socket nur macOS/Linux")
def test_socket_path_too_long_does_not_start(tmp_path: Path) -> None:
    path = "/tmp/" + "x" * 120
    result = subprocess.run(
        [sys.executable, "-m", "presidio_sidecar", "--uds", path],
        capture_output=True,
        text=True,
        timeout=EXIT_TIMEOUT_S,
        env={**os.environ, "PRESIDIO_STATE_DIR": str(tmp_path / "s")},
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "103 Byte" in result.stderr
