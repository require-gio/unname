"""Require binding to loopback only – enforced, not just a default (spec §2)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from presidio_sidecar.config import (
    DEFAULT_ALLOWED_ORIGINS,
    ConfigError,
    Settings,
    is_supported_device,
    parse_args,
)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.invalid", ""])
def test_non_loopback_is_rejected(host: str) -> None:
    with pytest.raises(ConfigError):
        Settings(host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "::1", "localhost"])
def test_loopback_is_accepted(host: str) -> None:
    assert Settings(host=host).host == host


def test_port_out_of_range(host: str = "127.0.0.1") -> None:
    with pytest.raises(ConfigError):
        Settings(host=host, port=70000)


def test_empty_token_is_rejected() -> None:
    with pytest.raises(ConfigError):
        Settings(token="")


def test_defaults() -> None:
    settings = parse_args([])
    assert settings.host == "127.0.0.1"
    assert settings.port == 0, "Zufallsport ist die Vorgabe"
    assert settings.allowed_origins == DEFAULT_ALLOWED_ORIGINS
    assert settings.exit_on_parent_death is True
    assert len(settings.token) >= 32


def test_token_is_generated_per_start() -> None:
    assert parse_args([]).token != parse_args([]).token


def test_env_token_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PII_SIDECAR_TOKEN", "von-der-huelle-vorgegeben")
    assert parse_args([]).token == "von-der-huelle-vorgegeben"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("auto", "auto"),
        ("cpu", "cpu"),
        ("mps", "mps"),
        ("cuda", "cuda"),
        ("cuda:0", "cuda:0"),
        ("cuda:17", "cuda:17"),
        (" CUDA:2 ", "cuda:2"),
    ],
)
def test_presidio_device_is_frozen_during_settings_build(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: str,
) -> None:
    monkeypatch.setenv("PRESIDIO_DEVICE", raw)

    settings = parse_args([])
    monkeypatch.setenv("PRESIDIO_DEVICE", "cpu" if expected != "cpu" else "mps")

    assert settings.presidio_device == expected
    assert is_supported_device(settings.presidio_device)


def test_empty_device_request_means_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRESIDIO_DEVICE", "   ")

    assert Settings().presidio_device == "auto"


@pytest.mark.parametrize("raw", ["gpu", "cuda:-1", "cuda:1.5", "cuda:", "mps:0"])
def test_unknown_device_request_remains_for_cpu_fallback(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("PRESIDIO_DEVICE", raw)

    settings = parse_args([])

    assert settings.presidio_device == raw
    assert not is_supported_device(settings.presidio_device)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("0", False), ("true", False), ("", False)],
)
def test_mps_fallback_is_frozen_during_settings_build(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", raw)

    settings = parse_args([])
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "0" if expected else "1")

    assert settings.mps_fallback_enabled is expected


def test_custom_origins_replace_default() -> None:
    settings = parse_args(["--allowed-origin", "tauri://localhost"])
    assert settings.allowed_origins == ("tauri://localhost",)


def test_phase_one_paths_and_limits_are_configurable(tmp_path: Path) -> None:
    import_path = tmp_path / "erp-export.csv"
    settings = parse_args(
        [
            "--state-directory",
            str(tmp_path / "state"),
            "--master-data-import-path",
            str(import_path),
            "--master-data-poll-seconds",
            "5",
            "--job-ttl-seconds",
            "30",
            "--job-timeout-seconds",
            "20",
            "--max-text-mb",
            "2.5",
            "--cpu-workers",
            "2",
            "--ner-threshold",
            "PERSON=0.51",
            "--ner-threshold",
            "ORGANIZATION=0.62",
        ]
    )

    assert settings.state_directory == tmp_path / "state"
    assert settings.master_data_import_path == import_path
    assert settings.master_data_poll_seconds == 5
    assert settings.job_ttl_seconds == 30
    assert settings.job_timeout_seconds == 20
    assert settings.max_text_bytes == int(2.5 * 1024 * 1024)
    assert settings.cpu_workers == 2
    assert dict(settings.ner_thresholds) == {
        "LOCATION": 0.45,
        "ORGANIZATION": 0.62,
        "PERSON": 0.51,
    }


def test_ner_thresholds_can_be_set_as_json_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PRESIDIO_NER_THRESHOLDS",
        '{"PERSON": 0.27, "ORGANIZATION": 0.73}',
    )

    settings = parse_args([])

    assert dict(settings.ner_thresholds) == {
        "LOCATION": 0.45,
        "ORGANIZATION": 0.73,
        "PERSON": 0.27,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_ttl_seconds", 0),
        ("job_timeout_seconds", 0),
        ("master_data_poll_seconds", 0),
        ("max_text_bytes", 0),
        ("cpu_workers", 5),
        ("ner_thresholds", (("PERSON", 1.1),)),
        ("presidio_device", 1),
        ("mps_fallback_enabled", 1),
    ],
)
def test_invalid_phase_one_limits_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ConfigError):
        Settings(**{field: value})  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-Domain-Socket nur macOS/Linux")
def test_unix_socket_path_checked_before_binding() -> None:
    import tempfile

    # On macOS the pytest tmp_path can be nearly as long as a Unix socket path.
    with tempfile.TemporaryDirectory(prefix="uds-", dir="/tmp") as directory:
        _check_socket_paths(Path(directory))


def _check_socket_paths(tmp_path: Path) -> None:
    private = tmp_path / "privat"
    private.mkdir(mode=0o700)
    assert Settings(unix_socket=private / "s").unix_socket == private / "s"

    with pytest.raises(ConfigError, match="absoluten"):
        Settings(unix_socket=Path("relativ/s"))
    with pytest.raises(ConfigError, match="103 Byte"):
        Settings(unix_socket=private / ("x" * 120))
    with pytest.raises(ConfigError, match="fehlt"):
        Settings(unix_socket=tmp_path / "gibt-es-nicht" / "s")
    (private / "s").write_text("")
    with pytest.raises(ConfigError, match="bereits"):
        Settings(unix_socket=private / "s")

    world_writable = tmp_path / "offen"
    world_writable.mkdir()
    os.chmod(world_writable, 0o777)
    with pytest.raises(ConfigError, match="nicht privat"):
        Settings(unix_socket=world_writable / "s")
