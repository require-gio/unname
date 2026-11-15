"""Standard output protocol between the sidecar and the Tauri harness.

The sidecar writes exactly one JSON line per event to stdout. The
handshake line ``ready`` carries the port (or socket path) and token — neither
may appear anywhere else, not in a file or command-line argument, where any
process in the session could read it.

Anything that is not a machine-readable event goes to stderr. Otherwise
the data stream the harness reads breaks down.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from . import PROTOCOL_VERSION


def emit(event: str, stream: TextIO | None = None, **fields: Any) -> None:
    """Write an event as a JSON line and flush the buffer immediately.

    Without ``flush``, lines would remain in the pipe until the buffer is
    full — the harness would appear to wait forever for the sidecar at startup.
    """
    out = stream if stream is not None else sys.stdout
    payload = {"event": event, "protocol": PROTOCOL_VERSION, **fields}
    out.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    out.flush()


def emit_ready(
    *,
    port: int | None = None,
    socket: str | None = None,
    token: str,
    pid: int,
    version: str,
    stream: TextIO | None = None,
) -> None:
    """Report that the service accepts connections.

    Exactly one of ``port`` (TCP on loopback) and ``socket`` (Unix domain
    socket) is present in the line.
    """
    if (port is None) == (socket is None):
        raise ValueError("Genau eins von port und socket angeben.")
    endpoint = {"port": port} if socket is None else {"socket": socket}
    emit("ready", stream=stream, **endpoint, token=token, pid=pid, version=version)


def emit_stopping(*, reason: str, stream: TextIO | None = None) -> None:
    emit("stopping", stream=stream, reason=reason)
