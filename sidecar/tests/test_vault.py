"""Security, determinism, and restart behavior of the token vault."""

from __future__ import annotations

import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import presidio_sidecar.vault as vault_module
from presidio_sidecar.vault import (
    ROTATION_WARNING,
    TokenVault,
    VaultCollisionError,
    VaultCryptoError,
    canonical_rank,
    normalize_value,
)


def test_hmac_has_a_fixed_type_bound_test_vector(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    secret = state / "token-vault.secret"
    secret.write_bytes(bytes(range(32)))
    secret.chmod(0o600)

    vault = TokenVault(state)
    try:
        assert vault.token_for("PERSON", "Hans  Meier", reversible=False) == "<<PERSON_1e8515ed>>"
    finally:
        vault.close()


def test_secret_is_created_once_with_private_permissions(tmp_path: Path) -> None:
    state = tmp_path / "private-state"
    first = TokenVault(state)
    first_secret = first.secret_path.read_bytes()
    first_status = first.status()
    first.close()

    second = TokenVault(state)
    try:
        assert second.secret_path.read_bytes() == first_secret
        assert len(first_secret) == 32
        assert first_status.fingerprint == second.status().fingerprint
        assert not second.mapping_path.exists()
        if os.name == "posix":
            assert stat.S_IMODE(state.stat().st_mode) == 0o700
            assert stat.S_IMODE(second.secret_path.stat().st_mode) == 0o600
    finally:
        second.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" Hans  Meier ", "hans meier"),
        ("HANS\u00a0MEIER", "hans meier"),
        ("Hans\n\tMeier", "hans meier"),
        ("Ｈａｎｓ　Ｍｅｉｅｒ", "hans meier"),
    ],
)
def test_normalization_nfkc_whitespace_and_spelling(value: str, expected: str) -> None:
    assert normalize_value(value) == expected


def test_empty_normalized_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="leer"):
        normalize_value("\u00a0 \n")


def test_hmac_token_is_stable_type_bound_and_thread_safe(tmp_path: Path) -> None:
    vault = TokenVault(tmp_path / "state")
    try:
        variants = ["Hans Meier", "hans  meier", "HANS\u00a0MEIER"] * 10
        with ThreadPoolExecutor(max_workers=4) as pool:
            tokens = tuple(
                pool.map(
                    lambda value: vault.token_for("person", value, reversible=False),
                    variants,
                )
            )
        assert len(set(tokens)) == 1
        assert re.fullmatch(r"<<PERSON_[0-9a-f]{8}>>", tokens[0])
        assert vault.token_for("organization", "Hans Meier", reversible=False) != tokens[0]
        assert not vault.mapping_path.exists()
    finally:
        vault.close()


def test_reversible_mapping_is_fully_encrypted_and_restart_safe(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    first = TokenVault(state)
    token = first.token_for("PERSON", "HANS MEIER", reversible=True)
    payload = first.mapping_path.read_bytes()
    first.close()

    assert b"SQLite format 3" not in payload
    assert b"HANS MEIER" not in payload
    assert b"hans meier" not in payload
    assert token.encode() not in payload
    if os.name == "posix":
        assert stat.S_IMODE((state / "token-vault.sqlite.enc").stat().st_mode) == 0o600

    second = TokenVault(state)
    try:
        same_token = second.token_for("PERSON", "Hans  Meier", reversible=True)
        result = second.deanonymize_tokens(f"Antwort für {same_token}.")
        assert same_token == token
        assert result.text == "Antwort für Hans  Meier."
        assert result.resolved == 1
        assert result.unknown == 0
    finally:
        second.close()


def test_canonical_spelling_can_only_move_up_in_rank(tmp_path: Path) -> None:
    state = tmp_path / "state"
    vault = TokenVault(state)
    token = vault.token_for("PERSON", "hans meier", reversible=True)
    vault.token_for("PERSON", "HANS MEIER", reversible=True)
    vault.token_for("PERSON", "Hans Meier", reversible=True)
    vault.token_for("PERSON", "hans meier", reversible=True)
    vault.close()

    restarted = TokenVault(state)
    try:
        assert restarted.deanonymize_tokens(token).text == "Hans Meier"
        assert canonical_rank("Hans Meier") > canonical_rank("HANS MEIER")
        assert canonical_rank("HANS MEIER") > canonical_rank("hans meier")
    finally:
        restarted.close()


def test_truncated_hmac_must_not_overwrite_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = TokenVault(tmp_path / "state")
    monkeypatch.setattr(vault, "_token_digest", lambda _entity, _value: "deadbeef" + "0" * 56)
    try:
        vault.token_for("PERSON", "Anna Beispiel", reversible=True)
        with pytest.raises(VaultCollisionError, match="kollidiert"):
            vault.token_for("PERSON", "Berta Beispiel", reversible=True)
        assert vault.deanonymize_tokens("<<PERSON_deadbeef>>").text == "Anna Beispiel"
    finally:
        vault.close()


def test_pseudonym_is_german_deterministic_and_restorable(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = TokenVault(state)
    token, surrogate = first.surrogate_for("PERSON", "Erika Musterfrau")
    again = first.surrogate_for("PERSON", "erika  musterfrau")
    first.close()

    second = TokenVault(state)
    try:
        assert again == (token, surrogate)
        assert surrogate != "Erika Musterfrau"
        result = second.deanonymize_surrogates(f"Guten Tag {surrogate}.")
        assert result.text == "Guten Tag Erika Musterfrau."
        assert result.resolved == 1
    finally:
        second.close()


def test_pseudonym_not_replaced_inside_foreign_word(
    tmp_path: Path,
) -> None:
    vault = TokenVault(tmp_path / "state")
    _, surrogate = vault.surrogate_for("PERSON", "Erika Musterfrau")
    try:
        embedded = vault.deanonymize_surrogates(f"X{surrogate}Y")
        exact = vault.deanonymize_surrogates(f"Hallo {surrogate}!")
        assert embedded.text == f"X{surrogate}Y"
        assert embedded.resolved == 0
        assert exact.text == "Hallo Erika Musterfrau!"
        assert exact.resolved == 1
    finally:
        vault.close()


def test_token_resolution_is_not_recursive(tmp_path: Path) -> None:
    vault = TokenVault(tmp_path / "state")
    token = vault.token_for("PERSON", "<<PERSON_deadbeef>>", reversible=True)
    try:
        result = vault.deanonymize_tokens(token)
        assert result.text == "<<PERSON_deadbeef>>"
        assert result.resolved == 1
        assert result.unknown == 0
    finally:
        vault.close()


def test_unknown_codes_stay_and_are_counted(tmp_path: Path) -> None:
    vault = TokenVault(tmp_path / "state")
    known = vault.token_for("PERSON", "Erika Musterfrau", reversible=True)
    unknown = "<<PERSON_deadbeef>>"
    try:
        result = vault.deanonymize_tokens(f"{known} / {unknown} / {unknown}")
        assert result.text == f"Erika Musterfrau / {unknown} / {unknown}"
        assert result.resolved == 1
        assert result.unknown == 2
        assert result.unknown_unique == 1
    finally:
        vault.close()


def test_manipulated_mapping_file_not_loaded_partially(tmp_path: Path) -> None:
    state = tmp_path / "state"
    vault = TokenVault(state)
    vault.token_for("PERSON", "Vertraulicher Name", reversible=True)
    mapping_path = vault.mapping_path
    vault.close()
    payload = bytearray(mapping_path.read_bytes())
    payload[-1] ^= 1
    mapping_path.write_bytes(payload)
    if os.name == "posix":
        mapping_path.chmod(0o600)

    restarted = TokenVault(state)
    try:
        with pytest.raises(VaultCryptoError, match="authentifiziert"):
            restarted.status()
    finally:
        restarted.close()


def test_insecure_secret_permissions_are_not_silently_repaired(tmp_path: Path) -> None:
    state = tmp_path / "state"
    vault = TokenVault(state)
    secret = vault.secret_path
    vault.close()
    if os.name != "posix":
        pytest.skip("POSIX-Dateirechte")
    secret.chmod(0o644)

    with pytest.raises(VaultCryptoError, match="0600"):
        TokenVault(state)


def test_mapping_write_error_is_not_left_as_ram_only_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    vault = TokenVault(state)
    original_write = vault_module._atomic_private_write
    failed = False

    def fail_mapping_once(path: Path, payload: bytes, *, replace: bool) -> None:
        nonlocal failed
        if path == vault.mapping_path and not failed:
            failed = True
            raise OSError("Datentraeger voll")
        original_write(path, payload, replace=replace)

    monkeypatch.setattr(vault_module, "_atomic_private_write", fail_mapping_once)
    with pytest.raises(OSError, match="voll"):
        vault.token_for("PERSON", "Erika Musterfrau", reversible=True)

    token = vault.token_for("PERSON", "Erika Musterfrau", reversible=True)
    vault.close()
    restarted = TokenVault(state)
    try:
        assert restarted.deanonymize_tokens(token).text == "Erika Musterfrau"
    finally:
        restarted.close()


def test_two_vault_instances_merge_mappings_without_last_writer_loss(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    first = TokenVault(state)
    anna = first.token_for("PERSON", "Anna Beispiel", reversible=True)
    second = TokenVault(state)
    berta = first.token_for("PERSON", "Berta Beispiel", reversible=True)
    clara = second.token_for("PERSON", "Clara Beispiel", reversible=True)
    first.close()
    second.close()

    restarted = TokenVault(state)
    try:
        result = restarted.deanonymize_tokens(f"{anna}, {berta}, {clara}")
        assert result.text == "Anna Beispiel, Berta Beispiel, Clara Beispiel"
        assert result.resolved == 3
    finally:
        restarted.close()


def test_vault_inside_detectable_project_tree_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "projekt"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    with pytest.raises(VaultCryptoError, match="Projekt"):
        TokenVault(project / "state")


def test_vault_at_the_repository_root_is_rejected(tmp_path: Path) -> None:
    """The repository root has no ``pyproject.toml`` of its own, only the sidecar has."""
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    (repository / "sidecar").mkdir()
    (repository / "sidecar" / "pyproject.toml").write_text(
        "[project]\nname='x'\n", encoding="utf-8"
    )

    with pytest.raises(VaultCryptoError, match="Projekt"):
        TokenVault(repository / "state")


def test_key_rotation_requires_explicit_confirmation(tmp_path: Path) -> None:
    vault = TokenVault(tmp_path / "state")
    token = vault.token_for("PERSON", "Erika Musterfrau", reversible=True)
    before = vault.status()
    with pytest.raises(ValueError, match="Kürzel"):
        vault.rotate_secret(confirm_loss=False)
    assert vault.status().fingerprint == before.fingerprint
    assert vault.deanonymize_tokens(token).resolved == 1

    after = vault.rotate_secret(confirm_loss=True)
    try:
        assert after.fingerprint != before.fingerprint
        assert after.mapping_count == 0
        assert after.rotation_warning == ROTATION_WARNING
        assert vault.deanonymize_tokens(token).unknown == 1
    finally:
        vault.close()
