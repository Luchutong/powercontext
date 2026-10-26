# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Canonical, independently verifiable context packs and prefix deltas."""

# Protocol failures carry their specific validation reason to the caller.
# ruff: noqa: TRY003

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ContextPackError(ValueError):
    """A context pack or delta violates the wire protocol."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """A complete ordered sequence of UTF-8 chunks and its content digest."""

    items: tuple[bytes, ...]
    _digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        items = _validated_items(self.items, field_name="items")
        object.__setattr__(self, "items", items)
        unsigned = {
            "schema": _PACK_SCHEMA,
            "items": [_encode_item(item) for item in items],
        }
        object.__setattr__(self, "_digest", _digest(unsigned))

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build a complete pack from its exact ordered chunks."""

        return cls(_validated_items(items, field_name="items"))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        """Split text deterministically into valid UTF-8 chunks within ``chunk_bytes``."""

        if not isinstance(content, str):
            raise ContextPackError("context content must be text")
        if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")

        chunks: list[bytes] = []
        current = bytearray()
        for character in content:
            try:
                encoded = character.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ContextPackError("context content must be valid UTF-8 text") from error
            if len(encoded) > chunk_bytes:
                raise ContextPackError("chunk_bytes cannot fit a UTF-8 character")
            if current and len(current) + len(encoded) > chunk_bytes:
                chunks.append(bytes(current))
                current.clear()
            current.extend(encoded)
        if current:
            chunks.append(bytes(current))
        return cls.build(tuple(chunks))

    @property
    def digest(self) -> str:
        """SHA-256 of the canonical schema and Base64 item list."""

        return self._digest

    @property
    def byte_count(self) -> int:
        """Length of the complete serialized pack, including this count field."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize using canonical UTF-8 JSON and a self-consistent byte count."""

        payload: dict[str, object] = {
            "schema": _PACK_SCHEMA,
            "items": [_encode_item(item) for item in self.items],
            "digest": self.digest,
            "byte_count": 0,
        }
        byte_count = 0
        while True:
            payload["byte_count"] = byte_count
            encoded = _canonical_json(payload)
            actual = len(encoded)
            if actual == byte_count:
                return encoded
            byte_count = actual

    @classmethod
    def from_bytes(cls, value: bytes) -> ContextPack:
        """Decode and verify a serialized complete pack."""

        payload = _load_json(value)
        fields = _object_fields(
            payload,
            expected={"schema", "items", "digest", "byte_count"},
            description="context pack",
        )
        if fields["schema"] != _PACK_SCHEMA:
            raise ContextPackError("unsupported context pack schema")
        items = _decode_items(fields["items"], field_name="items")
        pack = cls.build(items)
        if fields["digest"] != pack.digest:
            raise ContextPackError("context pack digest does not match its items")
        byte_count = fields["byte_count"]
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count != len(value):
            raise ContextPackError("context pack byte_count does not match its wire bytes")
        if pack.to_bytes() != value:
            raise ContextPackError("context pack JSON must use canonical encoding")
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A target pack represented by a reusable base prefix and appended chunks."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.base_digest, str) or not _DIGEST_PATTERN.fullmatch(self.base_digest):
            raise ContextPackError("base_digest must be a sha256 digest")
        if not isinstance(self.target_digest, str) or not _DIGEST_PATTERN.fullmatch(self.target_digest):
            raise ContextPackError("target_digest must be a sha256 digest")
        if not isinstance(self.prefix_items, int) or isinstance(self.prefix_items, bool) or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        object.__setattr__(self, "appended", _validated_items(self.appended, field_name="appended"))

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Encode a target using its longest exact chunk prefix shared with base."""

        prefix_items = 0
        maximum = min(len(base.items), len(target.items))
        while prefix_items < maximum and base.items[prefix_items] == target.items[prefix_items]:
            prefix_items += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_items,
            appended=target.items[prefix_items:],
        )

    @property
    def byte_count(self) -> int:
        """Length of the serialized delta, including all protocol overhead."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize using canonical UTF-8 JSON."""

        return _canonical_json({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [_encode_item(item) for item in self.appended],
        })

    @classmethod
    def from_bytes(cls, value: bytes) -> ContextPackDelta:
        """Decode and validate a serialized delta."""

        fields = _object_fields(
            _load_json(value),
            expected={"schema", "base_digest", "target_digest", "prefix_items", "appended"},
            description="context pack delta",
        )
        if fields["schema"] != _DELTA_SCHEMA:
            raise ContextPackError("unsupported context pack delta schema")
        prefix_items = fields["prefix_items"]
        if not isinstance(prefix_items, int) or isinstance(prefix_items, bool) or prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        return cls(
            base_digest=_string_field(fields["base_digest"], "base_digest"),
            target_digest=_string_field(fields["target_digest"], "target_digest"),
            prefix_items=prefix_items,
            appended=_decode_items(fields["appended"], field_name="appended"),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Verify a delta against its base and reconstruct the complete target pack."""

    if delta.base_digest != base.digest:
        raise ContextPackError("delta base_digest does not match the supplied base pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items exceeds the base pack")
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target_digest does not match the reconstructed pack")
    return target


def _validated_items(value: object, *, field_name: str) -> tuple[bytes, ...]:
    if not isinstance(value, tuple):
        raise ContextPackError(f"{field_name} must be a tuple of bytes")
    for item in value:
        if not isinstance(item, bytes):
            raise ContextPackError(f"every {field_name} item must be bytes")
        try:
            item.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextPackError(f"every {field_name} item must be valid UTF-8") from error
    return cast(tuple[bytes, ...], value)


def _encode_item(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_items(value: object, *, field_name: str) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError(f"{field_name} must be a Base64 string array")
    items: list[bytes] = []
    for encoded in value:
        if not isinstance(encoded, str):
            raise ContextPackError(f"every {field_name} item must be a Base64 string")
        try:
            raw = encoded.encode("ascii")
            item = base64.b64decode(raw, validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ContextPackError(f"{field_name} contains invalid Base64") from error
        if _encode_item(item) != encoded:
            raise ContextPackError(f"{field_name} contains non-canonical Base64")
        items.append(item)
    return _validated_items(tuple(items), field_name=field_name)


def _digest(value: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_json(value: bytes) -> object:
    if not isinstance(value, bytes):
        raise ContextPackError("wire payload must be bytes")
    try:
        return json.loads(value.decode("utf-8"), object_pairs_hook=_unique_object)
    except ContextPackError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as error:
        raise ContextPackError("wire payload must be valid UTF-8 JSON") from error


def _unique_object(pairs: list[tuple[str, object]], /) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ContextPackError("wire payload contains a duplicate JSON field")
        value[key] = item
    return value


def _object_fields(value: object, *, expected: set[str], description: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ContextPackError(f"{description} fields do not match the wire schema")
    return cast(dict[str, object], value)


def _string_field(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ContextPackError(f"{name} must be a string")
    return value


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]
