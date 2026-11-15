"""Sidecar startup configuration.

Binding to a loopback address is not a suggestion but a hard requirement: a PII service
bound to ``0.0.0.0`` would be reachable across the entire network. Such a startup is
rejected rather than corrected, so the error becomes visible.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

# During development, Vite serves the UI; in the built application, the WebView does.
# Other sources have no place here.
DEFAULT_ALLOWED_ORIGINS = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://127.0.0.1:1420",
    "http://localhost:1420",
)
DEFAULT_NER_THRESHOLDS = (
    ("PERSON", 0.35),
    ("ORGANIZATION", 0.40),
    ("LOCATION", 0.45),
)
SUPPORTED_DEVICE_PATTERN = re.compile(r"(?:auto|cpu|mps|cuda(?::[0-9]+)?)\Z")
#: ``sockaddr_un.sun_path`` fits 104 bytes on macOS including terminating null.
UNIX_SOCKET_PATH_MAX_BYTES = 103


class ConfigError(ValueError):
    """The sidecar was invoked with an invalid startup configuration."""


def default_state_directory() -> Path:
    """Private app directory, separated from the project and export folders."""

    override = os.environ.get("PRESIDIO_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "unname"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "unname"
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "unname"


def configured_device_request() -> str:
    """Device preference for this sidecar session.

    Known values are canonicalized to lowercase. An unknown value is preserved instead:
    hardware selection can thus fall back to CPU in a controlled manner and report the
    faulty preference in the diagnostic log, rather than terminating the sidecar hard
    during parsing.
    """

    raw = os.environ.get("PRESIDIO_DEVICE", "").strip()
    if not raw:
        return "auto"
    normalized = raw.casefold()
    return normalized if is_supported_device(normalized) else raw


def mps_fallback_from_environment() -> bool:
    """Only the documented PyTorch value ``1`` activates the mode."""

    return os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1"


def is_supported_device(value: str) -> bool:
    """Check supported Phase-5 device strings without importing Torch."""

    return bool(SUPPORTED_DEVICE_PATTERN.fullmatch(value))


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    #: 0 means: the operating system chooses a free port.
    port: int = 0
    #: Instead of TCP on loopback, use a Unix domain socket (0600) at this path. No port
    #: listens then, and the app does not need ``network.server``. ``host``/``port`` do
    #: not apply in that case.
    unix_socket: Path | None = None
    #: Generated per app startup, never written to disk.
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS
    #: Terminates the sidecar as soon as stdin closes — i.e., when the Tauri harness has
    #: disappeared. Prevents orphaned processes with open ports.
    exit_on_parent_death: bool = True
    #: Encrypted master data and its local key are located neither in the project nor in
    #: an export folder.
    state_directory: Path = field(default_factory=default_state_directory)
    #: Encrypted draft of entries. The harness specifies ``drafts.noindex`` in the app
    #: data folder; if unspecified, it resides under
    #: ``<state_directory>/drafts.noindex``.
    drafts_directory: Path | None = None
    #: Model directory of the harness. It holds the once-loaded and verified
    #: components; ``None`` means: bundle or development environment only.
    models_directory: Path | None = None
    #: Optional ERP/law-firm export, re-imported on change.
    master_data_import_path: Path | None = None
    master_data_poll_seconds: float = 60.0
    #: Idle time of loaded jobs: after this duration without access, they leave memory.
    #: Jobs of an entry reload from the draft; text-only jobs without an entry are then
    #: gone.
    job_ttl_seconds: float = 15 * 60
    #: A blocked model call must not occupy the pool unnoticed permanently. After
    #: timeout, /health reports an error; the harness restarts.
    job_timeout_seconds: float = 5 * 60
    max_text_bytes: int = 10 * 1024 * 1024
    #: ``None`` applies the core/RAM matrix; an override remains limited to 1…4.
    cpu_workers: int | None = None
    #: Configurable per entity; PRESIDIO_NER_THRESHOLDS can override individual settings.
    ner_thresholds: tuple[tuple[str, float], ...] = DEFAULT_NER_THRESHOLDS
    #: Frozen wish from PRESIDIO_DEVICE at build time. Unknown values remain for the
    #: diagnosed CPU fallback of hardware selection.
    presidio_device: str = field(default_factory=configured_device_request)
    #: Also frozen per start; later environment changes must not alter the already
    #: reported runtime state.
    mps_fallback_enabled: bool = field(default_factory=mps_fallback_from_environment)

    def __post_init__(self) -> None:
        if not is_loopback(self.host):
            raise ConfigError(
                f"host={self.host!r} ist keine Loopback-Adresse. "
                "Der Sidecar bindet ausschliesslich an 127.0.0.1 bzw. ::1."
            )
        if not 0 <= self.port <= 65535:
            raise ConfigError(f"port={self.port} liegt ausserhalb von 0…65535.")
        if not self.token:
            raise ConfigError("Leeres Bearer-Token.")
        if self.unix_socket is not None:
            validate_unix_socket_path(self.unix_socket)
        if self.master_data_poll_seconds <= 0:
            raise ConfigError("master_data_poll_seconds muss groesser als 0 sein.")
        if self.job_ttl_seconds <= 0:
            raise ConfigError("job_ttl_seconds muss groesser als 0 sein.")
        if self.job_timeout_seconds <= 0:
            raise ConfigError("job_timeout_seconds muss groesser als 0 sein.")
        if self.max_text_bytes <= 0:
            raise ConfigError("max_text_bytes muss groesser als 0 sein.")
        if self.cpu_workers is not None and not 1 <= self.cpu_workers <= 4:
            raise ConfigError("cpu_workers muss zwischen 1 und 4 liegen.")
        if not isinstance(self.presidio_device, str):
            raise ConfigError("presidio_device muss eine Zeichenkette sein.")
        if not isinstance(self.mps_fallback_enabled, bool):
            raise ConfigError("mps_fallback_enabled muss ein Boolean sein.")
        device = self.presidio_device.strip()
        if not device:
            device = "auto"
        normalized_device = device.casefold()
        object.__setattr__(
            self,
            "presidio_device",
            normalized_device if is_supported_device(normalized_device) else device,
        )
        normalized_thresholds: dict[str, float] = {}
        for item in self.ner_thresholds:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ConfigError("ner_thresholds braucht Paare aus Entitaet und Wert.")
            entity, value = item
            entity = entity.strip().upper()
            if not entity:
                raise ConfigError("NER-Entitaet darf nicht leer sein.")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigError(f"NER-Schwelle fuer {entity} ist keine Zahl.")
            if not 0.0 < float(value) <= 1.0:
                raise ConfigError(
                    f"NER-Schwelle fuer {entity} muss groesser 0 und hoechstens 1 sein."
                )
            normalized_thresholds[entity] = float(value)
        object.__setattr__(
            self,
            "ner_thresholds",
            tuple(sorted(normalized_thresholds.items())),
        )


def is_loopback(host: str) -> bool:
    """True only for addresses that do not leave the device."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_unix_socket_path(path: Path) -> None:
    """Validates the socket path before binding anything (fail-closed).

    The directory must exist, belong to the own user, and be writable by no one else:
    otherwise another process could replace the socket between check and bind. The path
    itself must not yet exist; an old socket is not silently removed.
    """

    if sys.platform == "win32":
        raise ConfigError("--uds gibt es nur auf macOS und Linux.")
    if not path.is_absolute():
        raise ConfigError("--uds braucht einen absoluten Pfad.")
    if len(os.fsencode(path)) > UNIX_SOCKET_PATH_MAX_BYTES:
        raise ConfigError(
            f"Socketpfad ist laenger als {UNIX_SOCKET_PATH_MAX_BYTES} Byte; "
            "der Sidecar startet nicht."
        )
    try:
        parent = os.stat(path.parent, follow_symlinks=False)
    except OSError as error:
        raise ConfigError(f"Ordner des Sockets fehlt: {error.strerror}") from error
    if not stat.S_ISDIR(parent.st_mode):
        raise ConfigError("Ordner des Sockets ist kein Verzeichnis.")
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise ConfigError("Ordner des Sockets ist nicht privat.")
    if os.path.lexists(path):
        raise ConfigError("Am Socketpfad liegt bereits etwas.")


def parse_args(argv: list[str] | None = None) -> Settings:
    parser = argparse.ArgumentParser(
        prog="presidio-sidecar",
        description="Lokaler PII-Sidecar (nur Loopback, Bearer-Token pro Start).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Nur Loopback-Adressen.")
    parser.add_argument(
        "--port", type=int, default=0, help="0 = freien Port vom Betriebssystem waehlen lassen."
    )
    parser.add_argument(
        "--uds",
        type=Path,
        metavar="PFAD",
        help="Unix-Domain-Socket statt TCP (macOS/Linux). Ersetzt --host/--port.",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        dest="allowed_origins",
        metavar="URL",
        help="Erlaubte CORS-Herkunft. Mehrfach angebbar. Ersetzt die Vorgabeliste.",
    )
    parser.add_argument(
        "--keep-running-without-parent",
        action="store_true",
        help="Nicht beenden, wenn stdin schliesst. Nur fuer manuelle Tests.",
    )
    parser.add_argument(
        "--state-directory",
        type=Path,
        help="Private Ablage fuer verschluesselte Stammdaten (nicht im Exportordner).",
    )
    parser.add_argument(
        "--drafts-dir",
        type=Path,
        help="Verschluesselter Arbeitsstand der Eintraege (von der Huelle vorgegeben).",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        help="Von der Huelle geprueft installierte Modelle (Phase I).",
    )
    parser.add_argument(
        "--master-data-import-path",
        type=Path,
        help="CSV/JSON-Datei eines regelmaessig aktualisierten Stammdatenexports.",
    )
    parser.add_argument(
        "--master-data-poll-seconds",
        type=float,
        default=float(os.environ.get("PRESIDIO_MASTER_DATA_POLL_SECONDS", "60")),
        help="Pruefintervall fuer den konfigurierten Stammdatenexport.",
    )
    parser.add_argument(
        "--job-ttl-seconds",
        type=float,
        default=float(os.environ.get("PRESIDIO_JOB_TTL_SECONDS", str(15 * 60))),
        help="Ruhezeit geladener Jobs im Arbeitsspeicher.",
    )
    parser.add_argument(
        "--job-timeout-seconds",
        type=float,
        default=float(os.environ.get("PRESIDIO_JOB_TIMEOUT_SECONDS", str(5 * 60))),
        help="Maximale Laufzeit eines Analysejobs vor fail-closed Neustart.",
    )
    parser.add_argument(
        "--max-text-mb",
        type=float,
        default=float(os.environ.get("PRESIDIO_MAX_TEXT_MB", "10")),
        help="Maximale Groesse eines Reintext-Jobs.",
    )
    parser.add_argument(
        "--cpu-workers",
        type=int,
        help="Optional 1…4; sonst automatische Dimensionierung nach Kernzahl und RAM.",
    )
    parser.add_argument(
        "--ner-threshold",
        action="append",
        metavar="ENTITY=WERT",
        help=(
            "NER-Schwelle je Entitaet, z. B. PERSON=0.35. Mehrfach angebbar; "
            "alternativ PRESIDIO_NER_THRESHOLDS als JSON-Objekt."
        ),
    )
    args = parser.parse_args(argv)

    origins = tuple(args.allowed_origins) if args.allowed_origins else DEFAULT_ALLOWED_ORIGINS

    return Settings(
        host=args.host,
        port=args.port,
        unix_socket=args.uds,
        # A token provided by the harness takes precedence; otherwise the sidecar
        # generates one and returns it via the handshake line.
        token=os.environ.get("PII_SIDECAR_TOKEN") or secrets.token_urlsafe(32),
        allowed_origins=origins,
        exit_on_parent_death=not args.keep_running_without_parent,
        state_directory=(args.state_directory or default_state_directory()).expanduser(),
        drafts_directory=(
            args.drafts_dir.expanduser()
            if args.drafts_dir is not None
            else _optional_path_from_env("PRESIDIO_DRAFTS_DIR")
        ),
        models_directory=(
            args.models_dir.expanduser()
            if args.models_dir is not None
            else _optional_path_from_env("PRESIDIO_MODELS_DIR")
        ),
        master_data_import_path=(
            args.master_data_import_path.expanduser()
            if args.master_data_import_path is not None
            else _optional_path_from_env("PRESIDIO_MASTER_DATA_PATH")
        ),
        master_data_poll_seconds=args.master_data_poll_seconds,
        job_ttl_seconds=args.job_ttl_seconds,
        job_timeout_seconds=args.job_timeout_seconds,
        max_text_bytes=max(1, int(args.max_text_mb * 1024 * 1024)),
        cpu_workers=args.cpu_workers,
        ner_thresholds=_configured_ner_thresholds(args.ner_threshold),
        presidio_device=configured_device_request(),
        mps_fallback_enabled=mps_fallback_from_environment(),
    )


def _optional_path_from_env(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _configured_ner_thresholds(
    cli_values: list[str] | None,
) -> tuple[tuple[str, float], ...]:
    thresholds = dict(DEFAULT_NER_THRESHOLDS)
    raw_environment = os.environ.get("PRESIDIO_NER_THRESHOLDS")
    if raw_environment:
        try:
            environment_values = json.loads(raw_environment)
        except json.JSONDecodeError as error:
            raise ConfigError("PRESIDIO_NER_THRESHOLDS muss ein JSON-Objekt sein.") from error
        if not isinstance(environment_values, dict):
            raise ConfigError("PRESIDIO_NER_THRESHOLDS muss ein JSON-Objekt sein.")
        for entity, value in environment_values.items():
            thresholds[str(entity)] = value

    for raw_value in cli_values or ():
        entity, separator, value = raw_value.partition("=")
        if not separator or not entity.strip():
            raise ConfigError("--ner-threshold erwartet ENTITY=WERT.")
        try:
            thresholds[entity] = float(value)
        except ValueError as error:
            raise ConfigError("--ner-threshold erwartet eine Zahl.") from error
    return tuple(thresholds.items())
