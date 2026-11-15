"""Short references in history: allocation, immutability, migration."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from presidio_sidecar import entries as entries_module
from presidio_sidecar.entries import (
    FORMAT_HEADER,
    NONCE_SIZE,
    REFERENCE_ALPHABET,
    REFERENCE_LENGTH,
    STORE_SCHEMA_VERSION,
    EntryError,
    EntryStore,
    allocate_reference,
)
from presidio_sidecar.masking import MaskingSpec

REFERENCE = re.compile(rf"^[{REFERENCE_ALPHABET}]{{{REFERENCE_LENGTH}}}$")


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "entries.enc", tmp_path / "entries.key"


def _store(tmp_path: Path, **kwargs: Any) -> EntryStore:
    path, key = _paths(tmp_path)
    return EntryStore(path, key, **kwargs)


def _sequence(*values: str) -> Callable[[int], str]:
    """Generator that yields the given values first, then random ones."""

    pending: Iterator[str] = iter(values)
    fallback = entries_module._random_reference
    lock = threading.Lock()

    def generate(length: int) -> str:
        with lock:
            value = next(pending, None)
        if value is None:
            return fallback(length)
        return value

    return generate


def _decrypt(store: EntryStore) -> dict[str, Any]:
    payload = store.path.read_bytes()
    nonce_end = len(FORMAT_HEADER) + NONCE_SIZE
    plaintext = store._cipher.decrypt(
        payload[len(FORMAT_HEADER) : nonce_end], payload[nonce_end:], FORMAT_HEADER
    )
    return json.loads(plaintext)


def _encrypt(store: EntryStore, document: dict[str, Any]) -> None:
    nonce = os.urandom(NONCE_SIZE)
    plaintext = json.dumps(document).encode("utf-8")
    store.path.write_bytes(
        FORMAT_HEADER + nonce + store._cipher.encrypt(nonce, plaintext, FORMAT_HEADER)
    )


def _legacy_store(tmp_path: Path, schema: int, count: int = 3) -> dict[str, Any]:
    """Writes a history in the given schema format without references."""

    start = datetime(2026, 9, 1, 8, tzinfo=UTC)
    clock = [start]
    store = _store(tmp_path, clock=lambda: clock[0])
    for index in range(count):
        clock[0] = start + timedelta(hours=index)
        store.create(
            kind="documents",
            masking=MaskingSpec.default(),
            title=f"Dokument {index}",
            draft_key=lambda entry_id: f"key-{entry_id}",
        )
    first = store.list()[-1]
    clock[0] = start + timedelta(days=2)
    store.update(
        first.id,
        lambda current: current.model_copy(
            update={"masking": MaskingSpec.default(), "restores": 2}
        ),
    )
    document = _decrypt(store)
    document["schema_version"] = schema
    for entry in document["entries"]:
        entry.pop("display_ref")
        if schema < 3:
            for field in ("draft_revision", "saved_revision", "exports"):
                entry.pop(field, None)
    if schema < 2:
        document.pop("draft_keys")
    _encrypt(store, document)
    return document


# -- Vergabe -------------------------------------------------------------------


def test_new_entries_get_unique_short_references(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = [
        store.create(kind="documents", masking=MaskingSpec.default(), title="Dokument")
        for _ in range(40)
    ]
    references = [entry.display_ref for entry in created]
    assert all(reference and REFERENCE.match(reference) for reference in references)
    assert len(set(references)) == len(references)
    assert _decrypt(store)["schema_version"] == STORE_SCHEMA_VERSION == 4


def test_collision_is_re_rolled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._reference_generator = _sequence("AAAAAA", "AAAAAA", "AAAAAA", "BBBBBB")
    first = store.create(kind="documents", masking=MaskingSpec.default(), title="A")
    second = store.create(kind="documents", masking=MaskingSpec.default(), title="B")
    assert first.display_ref == "AAAAAA"
    assert second.display_ref == "BBBBBB"


def test_after_many_collisions_reference_becomes_longer() -> None:
    taken = {"AAAAAA"}
    lengths: list[int] = []

    def generate(length: int) -> str:
        lengths.append(length)
        return "A" * length

    reference = allocate_reference(taken, generate=generate)
    assert reference == "AAAAAAA"
    assert lengths.count(REFERENCE_LENGTH) == entries_module.REFERENCE_ATTEMPTS
    assert lengths[-1] == REFERENCE_LENGTH + 1


def test_concurrent_creation_never_assigns_same_reference(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Each attempt initially proposes the same reference.
    store._reference_generator = _sequence(*(["CCCCCC"] * 200))
    barrier = threading.Barrier(8)
    results: list[str | None] = []
    lock = threading.Lock()

    def create() -> None:
        barrier.wait()
        entry = store.create(kind="documents", masking=MaskingSpec.default(), title="X")
        with lock:
            results.append(entry.display_ref)

    threads = [threading.Thread(target=create) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8
    assert len(set(results)) == 8
    stored = {entry.display_ref for entry in _store(tmp_path).list()}
    assert stored == set(results)


def test_reference_is_immutable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = store.create(kind="documents", masking=MaskingSpec.default(), title="Dokument")
    with pytest.raises(EntryError):
        store.update(entry.id, lambda current: current.model_copy(update={"display_ref": "ZZZZZZ"}))
    renamed = store.update(
        entry.id, lambda current: current.model_copy(update={"title": "Neu", "title_custom": True})
    )
    assert renamed.display_ref == entry.display_ref
    assert renamed.draft_revision == entry.draft_revision
    assert _store(tmp_path).get(entry.id).display_ref == entry.display_ref


def test_deleted_references_block_only_kept_entries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._reference_generator = _sequence("DDDDDD")
    first = store.create(kind="documents", masking=MaskingSpec.default(), title="A")
    store.delete(first.id)
    store._reference_generator = _sequence("DDDDDD")
    second = store.create(kind="documents", masking=MaskingSpec.default(), title="B")
    assert second.display_ref == "DDDDDD"


# -- Migration -----------------------------------------------------------------


@pytest.mark.parametrize("schema", [1, 2, 3])
def test_legacy_entries_get_references_once(tmp_path: Path, schema: int) -> None:
    legacy = _legacy_store(tmp_path, schema)
    before = {entry["id"]: entry for entry in legacy["entries"]}

    store = _store(tmp_path)
    migrated = store.list()
    assert len(migrated) == len(before)
    assert all(entry.display_ref and REFERENCE.match(entry.display_ref) for entry in migrated)
    assert len({entry.display_ref for entry in migrated}) == len(migrated)

    document = _decrypt(store)
    assert document["schema_version"] == 4
    for entry in migrated:
        original = before[entry.id]
        # Timestamp and content stay unchanged; only the reference is added:
        # Migration does not extend the retention period.
        assert entry.created_at == datetime.fromisoformat(original["created_at"])
        assert entry.updated_at == datetime.fromisoformat(original["updated_at"])
        assert entry.restores == original["restores"]
        assert entry.title == original["title"]
        if schema >= 3:
            assert entry.draft_revision == original["draft_revision"]
            assert entry.saved_revision == original["saved_revision"]
            assert len(entry.exports) == len(original["exports"])
    if schema >= 2:
        assert document["draft_keys"] == legacy["draft_keys"]
        for entry in migrated:
            assert store.draft_key(entry.id) == f"key-{entry.id}"


def test_migration_assigns_in_creation_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy_store(tmp_path, 3)
    # The generator must be effective already at load time.
    monkeypatch.setattr(
        entries_module, "_random_reference", _sequence("EEEEE2", "EEEEE3", "EEEEE4")
    )
    store = _store(tmp_path)
    ordered = sorted(store.list(), key=lambda entry: (entry.created_at, entry.id))
    assert [entry.display_ref for entry in ordered] == ["EEEEE2", "EEEEE3", "EEEEE4"]


def test_migrated_history_not_rewritten_on_reopen(
    tmp_path: Path,
) -> None:
    _legacy_store(tmp_path, 3)
    first = {entry.id: entry.display_ref for entry in _store(tmp_path).list()}
    raw = _paths(tmp_path)[0].read_bytes()

    reopened = _store(tmp_path)
    assert {entry.id: entry.display_ref for entry in reopened.list()} == first
    # Each write uses a fresh nonce; identical bytes indicate no write.
    assert _paths(tmp_path)[0].read_bytes() == raw


def test_existing_references_remain_duplicates_are_replaced(tmp_path: Path) -> None:
    legacy = _legacy_store(tmp_path, 3)
    ordered = sorted(legacy["entries"], key=lambda entry: (entry["created_at"], entry["id"]))
    ordered[0]["display_ref"] = "KEEP22"
    ordered[1]["display_ref"] = "KEEP22"
    store = _store(tmp_path)
    _encrypt(store, legacy)

    migrated = {entry.id: entry.display_ref for entry in _store(tmp_path).list()}
    assert migrated[ordered[0]["id"]] == "KEEP22"
    assert migrated[ordered[1]["id"]] not in {None, "KEEP22"}
    assert migrated[ordered[2]["id"]] is not None


def test_migration_write_failure_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy_store(tmp_path, 3)
    path = _paths(tmp_path)[0]
    raw = path.read_bytes()

    def fail(target: Path, payload: bytes) -> None:
        raise EntryError("Der Verlauf konnte nicht gespeichert werden.")

    monkeypatch.setattr(entries_module, "_atomic_private_write", fail)
    with pytest.raises(EntryError):
        _store(tmp_path)
    assert path.read_bytes() == raw

    monkeypatch.undo()
    recovered = _store(tmp_path).list()
    assert len(recovered) == 3
    assert all(entry.display_ref for entry in recovered)


def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(kind="documents", masking=MaskingSpec.default(), title="A")
    document = _decrypt(store)
    document["schema_version"] = 5
    _encrypt(store, document)
    with pytest.raises(EntryError):
        _store(tmp_path)
