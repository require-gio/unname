"""Encrypted draft of document entries.

After loading, each entry is self-contained: the original file, its extraction
and detection data are stored here, encrypted with a unique key per entry.
The sidecar never needs the original path again; an in-memory job is just a
view of this draft.

Dropzone layout::

    <drafts>/<entry_id>/<file_id>/manifest.enc   State, review data (last write)
                                  original.enc   Bytes as submitted
                                  extraction.enc Text, segments, boxes, OCR
                                  detection.enc  Findings, languages, scores

Key: a master key (``drafts.key``, 0600) encrypts a random 256-bit DEK per entry.
The encrypted DEK is stored in the history (``entries.enc``) alongside the entry.
When the entry is deleted, the DEK is removed in the same atomic write – any
remaining data become unreadable, even if file deletion fails
(crypto-shredding, spec §8).

Artifact format: marker ``UNNAME-DRAFT\x01``, a byte type, 8-byte nonce prefix,
then 1 MiB plaintext blocks, each encrypted with AES-256-GCM (STREAM construction).
The nonce is prefix‖block counter; the AAD binds marker, type, entry, file,
block number and a flag for the final block. Truncating, reordering or swapping
between artifacts, files and entries results in an error rather than corrupted
data, and both reading and writing use constant low memory.

Derived data (page images, layout maps, apply results) are never stored here;
they are regenerated on demand.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import struct
import threading
import time
import zlib
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .detection import DetectionResult, Finding, LanguageSegment
from .detection.second_opinion import Assessment
from .extraction import (
    BoundingBox,
    DocumentFormat,
    ExtractedDocument,
    SegmentKind,
    StructuredPayload,
    TextSegment,
)
from .registry_crypto import load_or_create_registry_key

FORMAT_HEADER: Final = b"UNNAME-DRAFT\x01"
KEY_HEADER: Final = b"UNNAME-DRAFT-KEY\x01"
#: Plaintext per block. A block fits comfortably in memory; the number of
#: blocks is limited only by the 32-bit counter.
BLOCK_SIZE: Final = 1024 * 1024
TAG_SIZE: Final = 16
NONCE_PREFIX_SIZE: Final = 8
KEY_NONCE_SIZE: Final = 12
#: Version of artifacts (manifest, extraction, detection). An unknown
#: version marks the file ``unavailable`` instead of crashing the sidecar.
SCHEMA_VERSION: Final = 1
_ID = re.compile(r"[0-9a-f]{16,64}\Z")
_TEMPORARY_SUFFIX = ".tmp"


class Artifact(StrEnum):
    MANIFEST = "manifest"
    ORIGINAL = "original"
    EXTRACTION = "extraction"
    DETECTION = "detection"
    VIEW = "view"


_ARTIFACT_CODES: Final = {
    Artifact.MANIFEST: 1,
    Artifact.ORIGINAL: 2,
    Artifact.EXTRACTION: 3,
    Artifact.DETECTION: 4,
    Artifact.VIEW: 5,
}

DraftState = Literal["ingested", "extracted", "detected", "failed", "cancelled"]


class DraftError(RuntimeError):
    """The draft could not safely perform an operation."""


class DraftUnavailableError(DraftError):
    """An artifact is missing, tampered with, or has an unknown schema."""


class DraftDeletedError(DraftError):
    """The entry (or file) is currently being deleted; do not write anything."""


class DraftManifest(BaseModel):
    """Commit point of a file: what exists, and in what state?

    Written last during every transition. Artifacts not listed in the
    manifest are considered non-existent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    file_id: str
    state: DraftState
    #: Content-free error code like on the job (``ocr_unavailable``, ``timeout`` …).
    error_code: str | None = None
    filename: str
    format: str
    size_bytes: int = Field(ge=0)
    source_sha256: str
    profile_id: str
    #: Information about how the document was read and detected (extractor, OCR,
    #: languages, second opinion, model). Used as basis for “re-detect” (spec §3.4).
    pipeline: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class EntryView(BaseModel):
    """View state per entry; offsets count UTF-16 code units (spec §2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    active_file_id: str | None = Field(default=None, max_length=64)
    representation: Literal["page", "faithful", "flow", "grid", "text"] = "page"
    page: int = Field(default=1, ge=1)
    anchor: int | None = Field(default=None, ge=0)
    focused_question_id: str | None = Field(default=None, max_length=256)
    preview: Literal["result", "original", "side_by_side"] = "result"


@dataclass(frozen=True, slots=True)
class DraftUsage:
    bytes: int
    entries: dict[str, int]


class DraftStore:
    """Drafts of all entries under a private folder; thread-safe.

    Writers register per entry (``_writing``). ``tombstone`` locks an entry and
    waits for ongoing writers to finish; afterwards, no one writes for it
    anymore – not even a worker finishing later.
    """

    def __init__(
        self,
        root: Path,
        key_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root)
        _prepare_private_directory(self.root)
        self._master = AESGCM(load_or_create_registry_key(Path(key_path)))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._gate = threading.Condition()
        self._writers: Counter[str] = Counter()
        self._tombstones: set[str] = set()
        #: Reading, modifying, writing a manifest – shared by all handles, since
        #: loaders and workers each open their own.
        self._manifest_lock = threading.Lock()

    # -- Key ---------------------------------------------------------

    def create_key(self, entry_id: str) -> str:
        """New DEK for an entry, wrapped with the master key."""

        _check_id(entry_id)
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(KEY_NONCE_SIZE)
        wrapped = nonce + self._master.encrypt(nonce, dek, KEY_HEADER + entry_id.encode())
        return base64.b64encode(wrapped).decode("ascii")

    def open(self, entry_id: str, wrapped_key: str) -> EntryDrafts:
        """Unpacks the DEK; raises ``DraftUnavailableError`` if that fails."""

        _check_id(entry_id)
        if self.is_deleted(entry_id):
            raise DraftDeletedError("Der Eintrag wird gerade geloescht.")
        try:
            wrapped = base64.b64decode(wrapped_key.encode("ascii"), validate=True)
            dek = self._master.decrypt(
                wrapped[:KEY_NONCE_SIZE],
                wrapped[KEY_NONCE_SIZE:],
                KEY_HEADER + entry_id.encode(),
            )
        except (InvalidTag, ValueError, UnicodeError) as error:
            raise DraftUnavailableError("Der Schluessel des Eintrags ist nicht lesbar.") from error
        if len(dek) != 32:
            raise DraftUnavailableError("Der Schluessel des Eintrags ist ungueltig.")
        return EntryDrafts(self, entry_id, AESGCM(dek))

    # -- Delete -----------------------------------------------------------

    def tombstone(self, entry_id: str, *, timeout: float = 30.0) -> None:
        """Locks an entry and waits for ongoing writers to finish."""

        _check_id(entry_id)
        deadline = time.monotonic() + timeout
        with self._gate:
            self._tombstones.add(entry_id)
            while self._writers[entry_id] > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DraftError("Ein Schreibvorgang des Eintrags endet nicht.")
                self._gate.wait(remaining)

    def untombstone(self, entry_id: str) -> None:
        """Releases the lock only if the deletion failed."""

        with self._gate:
            self._tombstones.discard(entry_id)

    def is_deleted(self, entry_id: str, file_id: str | None = None) -> bool:
        with self._gate:
            return entry_id in self._tombstones or (
                file_id is not None and f"{entry_id}/{file_id}" in self._tombstones
            )

    def delete_entry(self, entry_id: str) -> None:
        """Recursively removes the entry's folder."""

        _remove_tree(self._entry_directory(entry_id), self.root)

    def delete_file(self, entry_id: str, file_id: str) -> None:
        """An unreadable key must not prevent removal."""

        _check_id(entry_id)
        _check_id(file_id)
        self._forget_file(entry_id, file_id)
        _remove_tree(self._entry_directory(entry_id) / file_id, self._entry_directory(entry_id))

    def sweep(
        self,
        known: Mapping[str, Collection[str]],
        *,
        min_age_seconds: float = 15 * 60,
    ) -> int:
        """Cleans up folders without entries, files without a place, and leftover
        ``*.tmp`` files.
        files.

        ``known`` maps each entry with a draft to its file IDs.
        A folder is removed only if it has been unchanged for ``min_age_seconds``:
        Between
        placing an original and recording the file in the history, there is a short
        moment
        when no one knows about it yet.
        """

        cutoff = time.time() - min_age_seconds
        removed = 0
        try:
            candidates = list(self.root.iterdir())
        except FileNotFoundError:
            return 0
        for entry_directory in candidates:
            if not _is_plain_directory(entry_directory):
                if entry_directory.name.endswith(_TEMPORARY_SUFFIX) and _older(
                    entry_directory, cutoff
                ):
                    with suppress(OSError):
                        entry_directory.unlink()
                        removed += 1
                continue
            files = known.get(entry_directory.name)
            if files is None:
                if _ID.fullmatch(entry_directory.name) and _older(entry_directory, cutoff):
                    with suppress(OSError):
                        _remove_tree(entry_directory, self.root)
                        removed += 1
                continue
            for file_directory in list(entry_directory.iterdir()):
                if not _is_plain_directory(file_directory):
                    if file_directory.name.endswith(_TEMPORARY_SUFFIX) and _older(
                        file_directory, cutoff
                    ):
                        with suppress(OSError):
                            file_directory.unlink()
                            removed += 1
                    continue
                if file_directory.name not in files:
                    if _ID.fullmatch(file_directory.name) and _older(file_directory, cutoff):
                        with suppress(OSError):
                            _remove_tree(file_directory, entry_directory)
                            removed += 1
                    continue
                for leftover in file_directory.glob(f".*{_TEMPORARY_SUFFIX}"):
                    if _older(leftover, cutoff):
                        with suppress(OSError):
                            leftover.unlink()
                            removed += 1
        return removed

    def usage(self) -> DraftUsage:
        """Total and per entry occupied space, in bytes (for AS3.5 and the measurement)."""

        per_entry: dict[str, int] = {}
        try:
            candidates = list(self.root.iterdir())
        except FileNotFoundError:
            return DraftUsage(0, {})
        for entry_directory in candidates:
            if not _is_plain_directory(entry_directory):
                continue
            per_entry[entry_directory.name] = _tree_size(entry_directory)
        return DraftUsage(sum(per_entry.values()), per_entry)

    def has_entry(self, entry_id: str) -> bool:
        return self._entry_directory(entry_id).is_dir()

    # -- intern -------------------------------------------------------------

    def _entry_directory(self, entry_id: str) -> Path:
        _check_id(entry_id)
        return self.root / entry_id

    @contextmanager
    def _writing(self, entry_id: str, file_id: str) -> Iterator[None]:
        with self._gate:
            if entry_id in self._tombstones or f"{entry_id}/{file_id}" in self._tombstones:
                raise DraftDeletedError("Der Eintrag wird gerade geloescht.")
            self._writers[entry_id] += 1
        try:
            yield
        finally:
            with self._gate:
                self._writers[entry_id] -= 1
                if self._writers[entry_id] <= 0:
                    del self._writers[entry_id]
                self._gate.notify_all()

    def _forget_file(self, entry_id: str, file_id: str, *, timeout: float = 30.0) -> None:
        marker = f"{entry_id}/{file_id}"
        deadline = time.monotonic() + timeout
        with self._gate:
            self._tombstones.add(marker)
            while self._writers[entry_id] > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DraftError("Ein Schreibvorgang des Eintrags endet nicht.")
                self._gate.wait(remaining)


class EntryDrafts:
    """The draft state of an entry with an unpacked key."""

    def __init__(self, store: DraftStore, entry_id: str, cipher: AESGCM) -> None:
        self._store = store
        self.entry_id = entry_id
        self._cipher = cipher

    # -- Write ----------------------------------------------------------

    def write_view(self, view: EntryView) -> None:
        self._write("", Artifact.VIEW, _pack_json(view.model_dump(mode="json")))

    def write_original(self, file_id: str, payload: bytes) -> None:
        self._write(file_id, Artifact.ORIGINAL, memoryview(payload))

    def write_extraction(self, file_id: str, document: ExtractedDocument) -> None:
        self._write(file_id, Artifact.EXTRACTION, _pack_json(encode_document(document)))

    def write_detection(self, file_id: str, result: DetectionResult) -> None:
        self._write(file_id, Artifact.DETECTION, _pack_json(encode_detection(result)))

    def write_manifest(self, file_id: str, manifest: DraftManifest) -> None:
        if manifest.file_id != file_id:
            raise DraftError("Das Manifest gehoert zu einer anderen Datei.")
        payload = manifest.model_dump_json().encode("utf-8")
        self._write(file_id, Artifact.MANIFEST, _pack_json_bytes(payload))

    def update_manifest(self, file_id: str, **changes: Any) -> DraftManifest:
        """Reads, modifies, and writes a file's manifest atomically."""

        with self._store._manifest_lock:
            current = self.read_manifest(file_id)
            updated = DraftManifest.model_validate(
                {
                    **current.model_dump(),
                    **changes,
                    "updated_at": self._store._clock(),
                }
            )
            self.write_manifest(file_id, updated)
            return updated

    def delete_file(self, file_id: str) -> None:
        """Removes a file's draft; subsequent writers go into the void."""

        self._store.delete_file(self.entry_id, file_id)

    # -- Read --------------------------------------------------------------

    def read_view(self) -> EntryView | None:
        """The optional convenience state must never block the document."""

        if not self._artifact_path("", Artifact.VIEW).is_file():
            return None
        try:
            return EntryView.model_validate(_unpack_json(self._read("", Artifact.VIEW)))
        except (DraftUnavailableError, ValidationError):
            return None

    def read_manifest(self, file_id: str) -> DraftManifest:
        raw = _unpack_json_bytes(self._read(file_id, Artifact.MANIFEST))
        try:
            return DraftManifest.model_validate_json(raw)
        except ValidationError as error:
            raise DraftUnavailableError("Das Manifest ist ungueltig.") from error

    def read_extraction(
        self,
        file_id: str,
        *,
        structured: Callable[[str, DocumentFormat], StructuredPayload | None] | None = None,
    ) -> ExtractedDocument:
        return decode_document(
            _unpack_json(self._read(file_id, Artifact.EXTRACTION)), structured=structured
        )

    def read_detection(self, file_id: str) -> DetectionResult:
        return decode_detection(_unpack_json(self._read(file_id, Artifact.DETECTION)))

    def verify_original(self, file_id: str) -> None:
        """Authenticates the original without a plaintext file, before a new attempt."""

        for _block in _decrypt_blocks(
            self._cipher,
            self._artifact_path(file_id, Artifact.ORIGINAL),
            Artifact.ORIGINAL,
            self._aad(file_id, Artifact.ORIGINAL),
        ):
            pass

    def restore_original(self, file_id: str, destination: Path) -> int:
        """Decrypts the original block by block to ``destination`` (mode 0600)."""

        path = self._artifact_path(file_id, Artifact.ORIGINAL)
        aad = self._aad(file_id, Artifact.ORIGINAL)
        written = 0
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(destination, flags, 0o600)
        except OSError as error:
            raise DraftError("Das Original liess sich nicht bereitstellen.") from error
        try:
            with os.fdopen(descriptor, "wb") as target:
                for block in _decrypt_blocks(self._cipher, path, Artifact.ORIGINAL, aad):
                    target.write(block)
                    written += len(block)
        except BaseException:
            with suppress(OSError):
                destination.unlink()
            raise
        return written

    def has_file(self, file_id: str) -> bool:
        return self._artifact_path(file_id, Artifact.MANIFEST).is_file()

    # -- intern -------------------------------------------------------------

    def _file_directory(self, file_id: str) -> Path:
        _check_id(file_id)
        return self._store.root / self.entry_id / file_id

    def _artifact_path(self, file_id: str, artifact: Artifact) -> Path:
        if artifact is Artifact.VIEW:
            return self._store.root / self.entry_id / "view.enc"
        return self._file_directory(file_id) / f"{artifact.value}.enc"

    def _aad(self, file_id: str, artifact: Artifact) -> bytes:
        return b"".join(
            (
                FORMAT_HEADER,
                bytes((_ARTIFACT_CODES[artifact],)),
                _length_prefixed(self.entry_id.encode()),
                _length_prefixed(file_id.encode()),
            )
        )

    def _write(self, file_id: str, artifact: Artifact, payload: memoryview | bytes) -> None:
        path = self._artifact_path(file_id, artifact)
        directory = path.parent
        aad = self._aad(file_id, artifact)
        with self._store._writing(self.entry_id, file_id):
            try:
                _prepare_private_directory(self._store.root / self.entry_id)
                _prepare_private_directory(directory)
                _atomic_write(
                    path,
                    _encrypt_blocks(self._cipher, artifact, aad, memoryview(payload)),
                )
            except OSError as error:
                raise DraftError("Der Arbeitsstand liess sich nicht schreiben.") from error

    def _read(self, file_id: str, artifact: Artifact) -> bytes:
        path = self._artifact_path(file_id, artifact)
        aad = self._aad(file_id, artifact)
        return b"".join(_decrypt_blocks(self._cipher, path, artifact, aad))


# -- Block encryption -------------------------------------------------------


def _encrypt_blocks(
    cipher: AESGCM,
    artifact: Artifact,
    aad: bytes,
    payload: memoryview,
) -> Iterator[bytes]:
    prefix = os.urandom(NONCE_PREFIX_SIZE)
    yield FORMAT_HEADER + bytes((_ARTIFACT_CODES[artifact],)) + prefix
    count = max(1, -(-len(payload) // BLOCK_SIZE))
    for index in range(count):
        block = payload[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        last = index == count - 1
        yield cipher.encrypt(_nonce(prefix, index), bytes(block), _block_aad(aad, index, last))


def _decrypt_blocks(
    cipher: AESGCM,
    path: Path,
    artifact: Artifact,
    aad: bytes,
) -> Iterator[bytes]:
    header_size = len(FORMAT_HEADER) + 1 + NONCE_PREFIX_SIZE
    chunk = BLOCK_SIZE + TAG_SIZE
    try:
        stream = _open_private(path)
    except FileNotFoundError as error:
        raise DraftUnavailableError("Ein Artefakt des Arbeitsstands fehlt.") from error
    except OSError as error:
        raise DraftUnavailableError("Ein Artefakt des Arbeitsstands ist nicht lesbar.") from error
    with stream:
        size = os.fstat(stream.fileno()).st_size
        header = stream.read(header_size)
        if (
            len(header) != header_size
            or not header.startswith(FORMAT_HEADER)
            or header[len(FORMAT_HEADER)] != _ARTIFACT_CODES[artifact]
        ):
            raise DraftUnavailableError("Unbekanntes oder unvollstaendiges Arbeitsstandsformat.")
        prefix = header[len(FORMAT_HEADER) + 1 :]
        body = size - header_size
        count = max(1, -(-body // chunk))
        last_size = body - (count - 1) * chunk
        if last_size < TAG_SIZE or count > 0xFFFFFFFF:
            raise DraftUnavailableError("Das Artefakt ist abgeschnitten.")
        for index in range(count):
            last = index == count - 1
            sealed = stream.read(last_size if last else chunk)
            try:
                yield cipher.decrypt(_nonce(prefix, index), sealed, _block_aad(aad, index, last))
            except InvalidTag as error:
                raise DraftUnavailableError(
                    "Ein Artefakt des Arbeitsstands liess sich nicht authentifizieren."
                ) from error


def _nonce(prefix: bytes, index: int) -> bytes:
    return prefix + struct.pack(">I", index)


def _block_aad(aad: bytes, index: int, last: bool) -> bytes:
    return aad + struct.pack(">QB", index, 1 if last else 0)


def _length_prefixed(value: bytes) -> bytes:
    return struct.pack(">H", len(value)) + value


# -- JSON artifacts -------------------------------------------------------------


def _pack_json(value: Any) -> bytes:
    return _pack_json_bytes(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )


def _pack_json_bytes(raw: bytes) -> bytes:
    return zlib.compress(raw, level=6)


def _unpack_json_bytes(payload: bytes) -> bytes:
    try:
        return zlib.decompress(payload)
    except zlib.error as error:
        raise DraftUnavailableError("Ein Artefakt des Arbeitsstands ist beschaedigt.") from error


def _unpack_json(payload: bytes) -> Any:
    try:
        return json.loads(_unpack_json_bytes(payload))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise DraftUnavailableError("Ein Artefakt des Arbeitsstands ist beschaedigt.") from error


# -- Reading: ExtractedDocument ---------------------------------------------


def encode_document(document: ExtractedDocument) -> dict[str, Any]:
    """Everything that review, layout, and export need – without ``structured``.

    The structured view (DataFrame for CSV) is rebuilt when loading from the
    text; offsets are not attached to it.
    """

    return {
        "v": SCHEMA_VERSION,
        "text": document.text,
        "format": document.format.value,
        "source_name": document.source_name,
        "method": document.method.value,
        "ocr_used": document.ocr_used,
        "page_count": document.page_count,
        "text_layer_pages": list(document.text_layer_pages),
        "ocr_pages": list(document.ocr_pages),
        "image_size": list(document.image_size) if document.image_size is not None else None,
        "segments": [_encode_segment(segment) for segment in document.segments],
    }


def decode_document(
    data: Any,
    *,
    structured: Callable[[str, DocumentFormat], StructuredPayload | None] | None = None,
) -> ExtractedDocument:
    try:
        _check_version(data)
        text = data["text"]
        document_format = DocumentFormat(data["format"])
        image_size = data["image_size"]
        return ExtractedDocument(
            text=text,
            format=document_format,
            source_name=data["source_name"],
            method=data["method"],
            segments=tuple(_decode_segment(item) for item in data["segments"]),
            ocr_used=data["ocr_used"],
            page_count=data["page_count"],
            text_layer_pages=tuple(data["text_layer_pages"]),
            ocr_pages=tuple(data["ocr_pages"]),
            structured=structured(text, document_format) if structured is not None else None,
            image_size=(image_size[0], image_size[1]) if image_size is not None else None,
        )
    except DraftUnavailableError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise DraftUnavailableError("Die gespeicherte Auslesung ist ungueltig.") from error


def _encode_segment(segment: TextSegment) -> list[Any]:
    box = segment.bbox
    return [
        segment.start,
        segment.end,
        segment.kind.value,
        segment.source_ref,
        (
            [box.x0, box.y0, box.x1, box.y1, box.page_index, box.coordinate_space.value]
            if box is not None
            else None
        ),
        segment.confidence,
    ]


def _decode_segment(item: list[Any]) -> TextSegment:
    start, end, kind, source_ref, box, confidence = item
    return TextSegment(
        start=start,
        end=end,
        kind=SegmentKind(kind),
        source_ref=source_ref,
        bbox=(
            BoundingBox(
                x0=box[0],
                y0=box[1],
                x1=box[2],
                y1=box[3],
                page_index=box[4],
                coordinate_space=box[5],
            )
            if box is not None
            else None
        ),
        confidence=confidence,
    )


# -- Detection: DetectionResult ---------------------------------------------


def encode_detection(result: DetectionResult) -> dict[str, Any]:
    return {
        "v": SCHEMA_VERSION,
        "confirmed": [_encode_finding(finding) for finding in result.confirmed],
        "suspicious": [_encode_finding(finding) for finding in result.suspicious],
        "languages": [
            [segment.start, segment.end, segment.language, segment.detected]
            for segment in result.languages
        ],
        "assessments": {
            identifier: [
                assessment.label,
                assessment.confidence,
                assessment.protect,
                assessment.suggestion,
                assessment.decided,
                assessment.model,
            ]
            for identifier, assessment in result.assessments.items()
        },
    }


def decode_detection(data: Any) -> DetectionResult:
    try:
        _check_version(data)
        return DetectionResult(
            confirmed=tuple(_decode_finding(item) for item in data["confirmed"]),
            suspicious=tuple(_decode_finding(item) for item in data["suspicious"]),
            languages=tuple(
                LanguageSegment(start=item[0], end=item[1], language=item[2], detected=item[3])
                for item in data["languages"]
            ),
            assessments={
                identifier: Assessment(
                    label=item[0],
                    confidence=item[1],
                    protect=item[2],
                    suggestion=item[3],
                    decided=item[4],
                    model=item[5],
                )
                for identifier, item in data["assessments"].items()
            },
        )
    except DraftUnavailableError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        raise DraftUnavailableError("Die gespeicherte Erkennung ist ungueltig.") from error


def _encode_finding(finding: Finding) -> list[Any]:
    return [
        finding.entity_type,
        finding.start,
        finding.end,
        finding.score,
        str(finding.source),
        finding.text,
        finding.detector,
    ]


def _decode_finding(item: list[Any]) -> Finding:
    entity_type, start, end, score, source, text, detector = item
    return Finding(
        entity_type=entity_type,
        start=start,
        end=end,
        score=score,
        source=source,
        text=text,
        detector=detector,
    )


def _check_version(data: Any) -> None:
    if not isinstance(data, dict) or data.get("v") != SCHEMA_VERSION:
        raise DraftUnavailableError("Unbekanntes Schema des Arbeitsstands.")


# -- Separator -------------------------------------------------------------------


def _check_id(value: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DraftError("Ungueltige ID fuer den Arbeitsstand.")


def _prepare_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)


def _open_private(path: Path) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    return os.fdopen(descriptor, "rb")


def _atomic_write(path: Path, chunks: Iterator[bytes]) -> None:
    """Temporarily in the same folder → fsync → rename → fsync the folder."""

    temporary = path.with_name(f".{path.name}.{os.urandom(6).hex()}{_TEMPORARY_SUFFIX}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            for chunk in chunks:
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None  # type: ignore[assignment]
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_tree(path: Path, parent: Path) -> None:
    # Only direct children with ID names; do not follow any links.
    if path.parent != parent or not _ID.fullmatch(path.name):
        raise DraftError("Unsicherer Pfad im Arbeitsstand; Loeschung verweigert.")
    if path.is_symlink():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path, onexc=_ignore_missing)


def _ignore_missing(_function: Callable[..., object], _path: str, error: BaseException) -> None:
    if isinstance(error, FileNotFoundError):
        return
    raise error


def _is_plain_directory(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _older(path: Path, cutoff: float) -> bool:
    try:
        return path.lstat().st_mtime <= cutoff
    except FileNotFoundError:
        return False


def _tree_size(path: Path) -> int:
    total = 0
    for current, _directories, files in os.walk(path):
        for name in files:
            with suppress(OSError):
                total += (Path(current) / name).lstat().st_size
    return total


__all__ = [
    "BLOCK_SIZE",
    "Artifact",
    "DraftDeletedError",
    "DraftError",
    "DraftManifest",
    "DraftState",
    "DraftStore",
    "DraftUnavailableError",
    "DraftUsage",
    "EntryDrafts",
    "decode_detection",
    "decode_document",
    "encode_detection",
    "encode_document",
]
