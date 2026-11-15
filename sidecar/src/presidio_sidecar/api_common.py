"""Common HTTP helpers for routers: limited reading and UTF-16 offsets."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, ValidationError
from starlette.status import HTTP_413_CONTENT_TOO_LARGE, HTTP_422_UNPROCESSABLE_CONTENT

from .processing import ProcessingValidationError


async def read_json_object(request: Request, *, max_bytes: int) -> dict[str, Any]:
    raw = await read_limited_body(request, max_bytes=max_bytes)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Ungueltiges UTF-8-JSON.",
        ) from error
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Der JSON-Body muss ein Objekt sein.",
        )
    return payload


async def read_limited_body(request: Request, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(
                status_code=HTTP_413_CONTENT_TOO_LARGE,
                detail="Anfrage ist zu gross.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def utf16_offset(text: str, python_offset: int) -> int:
    return len(text[:python_offset].encode("utf-16-le")) // 2


def python_offset_from_utf16(text: str, utf16_offset: object) -> int:
    if isinstance(utf16_offset, bool) or not isinstance(utf16_offset, int):
        raise ProcessingValidationError("Entity-Offsets müssen ganze Zahlen sein.")
    if utf16_offset < 0:
        raise ProcessingValidationError("Entity-Offsets dürfen nicht negativ sein.")
    units = 0
    for index, character in enumerate(text):
        if units == utf16_offset:
            return index
        units += 2 if ord(character) > 0xFFFF else 1
        if units > utf16_offset:
            raise ProcessingValidationError("Entity-Offset teilt ein UTF-16-Ersatzzeichenpaar.")
    if units == utf16_offset:
        return len(text)
    raise ProcessingValidationError("Entity-Offset liegt ausserhalb des Textes.")


_ASTRAL = re.compile("[\U00010000-\U0010ffff]")


class Utf16Offsets:
    """Convert UTF-16 offsets in a text to Python indices, built once per text.

    ``python_offset_from_utf16`` iterates from the text start for each offset;
    saving the map avoided quadratic performance for thousands of offsets in
    long documents. Without characters outside the BMP, both counts are equal,
    so no map needs building.
    """

    __slots__ = ("_length", "_index", "_units")

    def __init__(self, text: str) -> None:
        self._length = len(text)
        self._index: dict[int, int] | None = None
        self._units = self._length
        if _ASTRAL.search(text) is None:
            return
        index: dict[int, int] = {}
        units = 0
        for position, character in enumerate(text):
            index[units] = position
            units += 2 if ord(character) > 0xFFFF else 1
        index[units] = self._length
        self._index = index
        self._units = units

    def python(self, offset: int, error: type[Exception]) -> int:
        """Convert Python index to ``offset``; raise ``error`` with the same message as before."""

        if self._index is None:
            if offset > self._length:
                raise error("Offset liegt ausserhalb des Textes.")
            return offset
        position = self._index.get(offset)
        if position is not None:
            return position
        if offset < self._units:
            raise error("Offset teilt ein UTF-16-Ersatzzeichenpaar.")
        raise error("Offset liegt ausserhalb des Textes.")


def parse_model[ModelT: BaseModel](model: type[ModelT], payload: object) -> ModelT:
    """Validate a JSON body against an API model; errors contain no submitted values."""

    try:
        return model.model_validate(payload)
    except ValidationError as error:
        details = "; ".join(
            ".".join(str(item) for item in detail["loc"]) + f": {detail['msg']}"
            for detail in error.errors(include_input=False, include_url=False)
        )
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Ungueltige Anfrage ({details}).",
        ) from error


__all__ = [
    "parse_model",
    "Utf16Offsets",
    "python_offset_from_utf16",
    "read_json_object",
    "read_limited_body",
    "utf16_offset",
]
