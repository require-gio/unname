"""Sidecar entry point.

Startup sequence:

1. Bind the socket to a loopback address, let the OS choose the port, and
   read it *afterwards*. This fixes the port before anyone else learns it,
   preventing a second process from grabbing it. With ``--uds`` (macOS), use
   a Unix domain socket with 0600 permissions in the private folder specified
   by the harness; no port is then listened on.
2. Start Uvicorn on exactly this socket.
3. Once the service accepts connections: write a handshake line with the port
   or socket path and token to stdout (Spec. 2).

stdout belongs exclusively to the protocol. All log output goes to stderr,
otherwise the data stream the harness reads will break.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from . import __version__
from .app import create_app
from .config import ConfigError, Settings, parse_args
from .diagnostics import DiagnosticsRecorder
from .handshake import emit_ready, emit_stopping
from .resources import BundledAssetError, initialize_runtime, ocr_missing_reason

#: Libraries whose INFO lines reveal nothing about operation. Presidio
#: reports each Recognizer as “Loaded recognizer: …” — and Recognizers are
#: created anew for each analysis so that countries and terms from the
#: settings take effect immediately. That amounted to about 25 lines per
#: document.
QUIET_LOGGERS = ("presidio-analyzer", "presidio-anonymizer")


def configure_logging() -> None:
    """A format for everything on stderr; the harness reads the level from it.

    ``%(levelname)s`` appears at a fixed position after the timestamp
    (``sidecar_log_level`` in ``app/src-tauri/src/sidecar.rs``).
    """

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # Python warnings (e.g., deprecations) otherwise appear raw and multi-line
    # on stderr without a level. Via ``logging``, they arrive as a single
    # WARNING.
    logging.captureWarnings(True)


def bind_socket(host: str, port: int) -> socket.socket:
    """Binds the listening socket and returns it open."""
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    # No SO_REUSEPORT: a second sidecar must not bind to the same port. An
    # “address in use” error is the correct response here.
    sock.bind((host, port))
    sock.listen(128)
    return sock


def bind_unix_socket(path: Path) -> socket.socket:
    """Binds a Unix domain socket that carries 0600 permissions from the start.

    The umask applies during ``bind``, ensuring no time window with broader
    permissions exists; ``chmod`` afterwards explicitly enforces this.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous = os.umask(0o177)
    try:
        sock.bind(os.fspath(path))
    except OSError:
        sock.close()
        raise
    finally:
        os.umask(previous)
    try:
        os.chmod(path, 0o600)
        sock.listen(128)
    except OSError:
        sock.close()
        _remove_socket(path)
        raise
    return sock


def _remove_socket(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


def watch_parent(server: uvicorn.Server) -> None:
    """Terminates the service as soon as stdin closes.

    If the Tauri harness disappears, a process with an open port and loaded
    models would otherwise remain, controlled by no one.
    """

    def _wait() -> None:
        try:
            while sys.stdin.readline():
                pass
        except (ValueError, OSError):
            pass
        server.should_exit = True

    thread = threading.Thread(target=_wait, name="parent-watchdog", daemon=True)
    thread.start()


#: How long an idle connection stays open. Uvicorn otherwise closes after
#: 5 s — exactly in sync with the harness’s liveness check (every 5 s), and
#: a ping sometimes hit a connection that was closing here. The harness
#: gives up on idle connections after 2 s (``POOL_IDLE_TIMEOUT`` in
#: ``app/src-tauri/src/sidecar.rs``); it should always close first.
KEEP_ALIVE_SECONDS = 30


def run(settings: Settings) -> int:
    configure_logging()
    diagnostics = DiagnosticsRecorder()

    try:
        if settings.unix_socket is not None:
            sock = bind_unix_socket(settings.unix_socket)
        else:
            sock = bind_socket(settings.host, settings.port)
    except OSError as error:
        # Fail-closed: no half-started service. The harness sees the exit code
        # and locks the UI.
        print(f"Sidecar konnte nicht binden: {error}", file=sys.stderr)
        return 2

    socket_path: str | None = None
    bound_port: int | None = None
    if settings.unix_socket is not None:
        socket_path = os.fspath(settings.unix_socket)
        # The path is in the app container; for diagnostics, the connection type
        # is sufficient.
        details: dict[str, object] = {"transport": "unix"}
    else:
        bound_host, bound_port = sock.getsockname()[:2]
        details = {"transport": "tcp", "host": bound_host, "port": bound_port}

    def announce_ready() -> None:
        emit_ready(
            port=bound_port,
            socket=socket_path,
            token=settings.token,
            pid=os.getpid(),
            version=__version__,
        )
        diagnostics.record(
            "startup",
            "Sidecar nimmt Verbindungen an.",
            **details,
            allowed_origins=list(settings.allowed_origins),
        )
        # Without text recognition, scans fail only at the first document; here
        # it is known at startup what the cause is.
        ocr_reason = ocr_missing_reason()
        if ocr_reason is not None:
            diagnostics.record(
                "model_load",
                f"Texterkennung nicht verfuegbar: {ocr_reason}",
                severity="warning",
                component="ocr",
            )
        # Announce first, then warm up: The UI becomes usable immediately, and the
        # language model is ready before the first document waits for it. Previously,
        # this exact task accounted for the entire setup time.
        app.state.phase_one.start_warmup()

    def announce_stopping() -> None:
        diagnostics.record("shutdown", "Sidecar faehrt herunter.", severity="info")
        emit_stopping(reason="shutdown")
        # Even here: After SIGTERM, uvicorn triggers the signal again at the end, so the
        # finally block below will not run.
        if settings.unix_socket is not None:
            _remove_socket(settings.unix_socket)

    app = create_app(
        settings,
        diagnostics,
        on_started=announce_ready,
        on_stopping=announce_stopping,
    )

    config = uvicorn.Config(
        app,
        # Logging is configured above; uvicorn must not overwrite it and accidentally
        # write to stdout again.
        log_config=None,
        access_log=False,
        # A job should be allowed to finish, but not indefinitely.
        timeout_graceful_shutdown=10,
        timeout_keep_alive=KEEP_ALIVE_SECONDS,
    )
    server = uvicorn.Server(config)

    if settings.exit_on_parent_death:
        watch_parent(server)

    try:
        server.run(sockets=[sock])
    finally:
        if settings.unix_socket is not None:
            _remove_socket(settings.unix_socket)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        initialize_runtime()
    except BundledAssetError as error:
        print(f"Gebundelte Sidecar-Ressourcen ungueltig: {error}", file=sys.stderr)
        return 2
    try:
        settings = parse_args(argv)
    except ConfigError as error:
        print(f"Ungueltige Startkonfiguration: {error}", file=sys.stderr)
        return 2
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
